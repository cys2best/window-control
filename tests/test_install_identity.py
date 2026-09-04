import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

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
