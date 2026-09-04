import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import platform

import pytest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from server import install_identity


def test_get_or_create_install_keypair_persists_and_is_reused(tmp_path, monkeypatch):
    monkeypatch.setattr(install_identity, "_CANDIDATE_DIRS", [str(tmp_path)])

    private_key, public_key = install_identity.get_or_create_install_keypair()
    private_key2, public_key2 = install_identity.get_or_create_install_keypair()

    assert isinstance(private_key, Ed25519PrivateKey)
    assert public_key == public_key2
    assert os.path.isfile(tmp_path / "install_key.bin")


def test_get_or_create_install_keypair_falls_back_in_memory_when_unwritable(tmp_path, monkeypatch):
    blocked = tmp_path / "not_a_directory"
    blocked.write_text("i am a file, not a dir")
    monkeypatch.setattr(install_identity, "_CANDIDATE_DIRS", [str(blocked / "sub")])

    private_key, public_key = install_identity.get_or_create_install_keypair()

    assert isinstance(private_key, Ed25519PrivateKey)
    assert isinstance(public_key, str) and public_key


def test_get_cached_owner_user_id_returns_none_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(install_identity, "_CANDIDATE_DIRS", [str(tmp_path)])

    assert install_identity.get_cached_owner_user_id() is None


def test_set_then_get_cached_owner_user_id_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setattr(install_identity, "_CANDIDATE_DIRS", [str(tmp_path)])

    install_identity.set_cached_owner_user_id("user-123")

    assert install_identity.get_cached_owner_user_id() == "user-123"


def test_set_cached_owner_user_id_falls_back_silently_when_unwritable(tmp_path, monkeypatch):
    blocked = tmp_path / "not_a_directory"
    blocked.write_text("i am a file, not a dir")
    monkeypatch.setattr(install_identity, "_CANDIDATE_DIRS", [str(blocked / "sub")])

    install_identity.set_cached_owner_user_id("user-123")  # must not raise

    assert install_identity.get_cached_owner_user_id() is None


@pytest.mark.skipif(
    platform.system() == "Windows" or (hasattr(os, "geteuid") and os.geteuid() == 0),
    reason="requires a real filesystem where chmod actually blocks the owner (not Windows, not root)",
)
def test_set_cached_owner_user_id_rewrites_stale_value_when_top_dir_becomes_unwritable(tmp_path, monkeypatch):
    """Regression test for the read/write directory divergence bug: if the
    top-priority directory holds a stale cached value but becomes unwritable
    (while staying readable), set_cached_owner_user_id must still make the
    *next* get_cached_owner_user_id() call see the fresh value -- not the
    stale one left behind in the top-priority directory.
    """
    dir_a = tmp_path / "dir_a"
    dir_b = tmp_path / "dir_b"
    dir_a.mkdir()
    dir_b.mkdir()
    monkeypatch.setattr(install_identity, "_CANDIDATE_DIRS", [str(dir_a), str(dir_b)])

    install_identity.set_cached_owner_user_id("user-old")
    stale_file = dir_a / "install_owner.txt"
    assert stale_file.is_file()
    assert install_identity.get_cached_owner_user_id() == "user-old"

    # Make the existing file itself unwritable (directory stays writable) --
    # this is what an AV lock / permission change on the file looks like,
    # and it's still readable, matching the bug's failure mode.
    os.chmod(stale_file, 0o400)
    try:
        install_identity.set_cached_owner_user_id("user-new")

        assert install_identity.get_cached_owner_user_id() == "user-new"
    finally:
        # The fix deletes the stale copy as part of the write, but restore
        # permissions defensively in case it's still there (e.g. the fix
        # regresses) so tmp_path teardown never fails on a read-only file.
        if stale_file.exists():
            os.chmod(stale_file, 0o600)


def test_get_or_create_install_keypair_regenerates_when_existing_key_is_corrupt(tmp_path, monkeypatch):
    """A truncated/garbage key file means "there is no usable key here" and
    must self-heal exactly like "no key file exists yet" does. Before the
    fix, from_private_bytes() raised straight out of this function and
    through create_app()/build_engine_orchestrator(), crashing startup with
    no recovery path -- contradicting this module's graceful-degradation
    docstring.
    """
    monkeypatch.setattr(install_identity, "_CANDIDATE_DIRS", [str(tmp_path)])
    key_file = tmp_path / "install_key.bin"
    key_file.write_bytes(b"not-a-valid-ed25519-key")

    private_key, public_key = install_identity.get_or_create_install_keypair()

    assert isinstance(private_key, Ed25519PrivateKey)
    assert isinstance(public_key, str) and public_key
    # The corrupt file must be replaced, so the regenerated key is stable
    # across restarts rather than churning on every boot.
    _, public_key2 = install_identity.get_or_create_install_keypair()
    assert public_key2 == public_key


def test_get_or_create_install_keypair_regenerates_when_existing_key_is_truncated(tmp_path, monkeypatch):
    """The realistic corruption shape: a crash partway through the 32-byte
    write leaves a short-but-nonempty file.
    """
    monkeypatch.setattr(install_identity, "_CANDIDATE_DIRS", [str(tmp_path)])
    good = Ed25519PrivateKey.generate().private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    (tmp_path / "install_key.bin").write_bytes(good[:11])

    private_key, public_key = install_identity.get_or_create_install_keypair()

    assert isinstance(private_key, Ed25519PrivateKey)
    assert public_key


def test_key_write_is_atomic_and_never_exposes_a_partial_file(tmp_path, monkeypatch):
    """The destination path must go from absent to complete in one step, so
    a crash can never leave a half-written key for the next boot to read.
    """
    monkeypatch.setattr(install_identity, "_CANDIDATE_DIRS", [str(tmp_path)])
    key_file = tmp_path / "install_key.bin"
    observed = []
    real_replace = os.replace

    def recording_replace(src, dst, *args, **kwargs):
        # Snapshot the destination immediately before the atomic swap: it
        # must not exist yet (a non-atomic write would have already created
        # and truncated it by now).
        observed.append(os.path.exists(dst))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(install_identity.os, "replace", recording_replace)

    _, public_key = install_identity.get_or_create_install_keypair()

    assert observed == [False], "expected exactly one os.replace onto a not-yet-existing path"
    assert len(key_file.read_bytes()) == 32
    assert public_key


def test_interrupted_key_write_leaves_no_file_at_all(tmp_path, monkeypatch):
    """Simulate the interruption: if the process dies before the swap, the
    real filename must be untouched (absent) rather than truncated, and the
    call still degrades to a usable in-memory keypair.
    """
    monkeypatch.setattr(install_identity, "_CANDIDATE_DIRS", [str(tmp_path)])

    def dying_replace(src, dst, *args, **kwargs):
        raise OSError("interrupted before the swap")

    monkeypatch.setattr(install_identity.os, "replace", dying_replace)

    private_key, public_key = install_identity.get_or_create_install_keypair()

    assert isinstance(private_key, Ed25519PrivateKey)
    assert public_key
    assert not (tmp_path / "install_key.bin").exists()


def test_successful_write_leaves_no_temp_files_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(install_identity, "_CANDIDATE_DIRS", [str(tmp_path)])

    install_identity.get_or_create_install_keypair()
    install_identity.set_cached_owner_user_id("user-123")

    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "install_key.bin", "install_owner.txt",
    ]


def test_failed_write_leaves_no_temp_files_behind(tmp_path, monkeypatch):
    monkeypatch.setattr(install_identity, "_CANDIDATE_DIRS", [str(tmp_path)])

    def dying_replace(src, dst, *args, **kwargs):
        raise OSError("interrupted before the swap")

    monkeypatch.setattr(install_identity.os, "replace", dying_replace)

    install_identity.get_or_create_install_keypair()

    assert list(tmp_path.iterdir()) == []


def test_set_cached_owner_user_id_falls_back_when_preferred_dir_write_fails(tmp_path, monkeypatch):
    """Directory-divergence coverage that doesn't depend on filesystem
    permission semantics: the top-priority directory holds a stale value but
    can no longer be written, so the fresh value has to land in a lower
    candidate AND the stale copy has to be removed, or the next read
    returns the stale value first.
    """
    dir_a = tmp_path / "dir_a"
    dir_b = tmp_path / "dir_b"
    dir_a.mkdir()
    dir_b.mkdir()
    monkeypatch.setattr(install_identity, "_CANDIDATE_DIRS", [str(dir_a), str(dir_b)])
    install_identity.set_cached_owner_user_id("user-old")
    assert (dir_a / "install_owner.txt").is_file()

    real_write_to_dir = install_identity._write_to_dir

    def failing_write_to_dir(directory, filename, data):
        if directory == str(dir_a):
            return False
        return real_write_to_dir(directory, filename, data)

    monkeypatch.setattr(install_identity, "_write_to_dir", failing_write_to_dir)

    install_identity.set_cached_owner_user_id("user-new")

    assert install_identity.get_cached_owner_user_id() == "user-new"
    assert not (dir_a / "install_owner.txt").exists()
