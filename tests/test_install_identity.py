import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import platform

import pytest

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
