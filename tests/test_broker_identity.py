import json
import pytest
from broker.identity_store import InstallationStore, InstallationIdentity


def test_installation_registration_and_authentication(tmp_path):
    store_file = tmp_path / "installations.json"
    store = InstallationStore(storage_path=store_file)

    identity = store.register()
    assert len(identity.installation_id) == 32 or len(identity.installation_id) == 36
    assert len(identity.credential) > 20

    # Digest only in storage, no plaintext credential
    assert store_file.exists()
    stored_data = json.loads(store_file.read_text())
    assert identity.installation_id in stored_data
    stored_record = stored_data[identity.installation_id]
    assert identity.credential not in str(stored_record)
    assert "digest" in stored_record

    # Successful authentication
    assert store.authenticate(identity.installation_id, identity.credential) is True

    # Failed authentication with wrong credential
    assert store.authenticate(identity.installation_id, "wrong_credential") is False

    # Failed authentication with unknown installation
    assert store.authenticate("00000000000000000000000000000000", identity.credential) is False


def test_restart_preserves_installation_credentials(tmp_path):
    store_file = tmp_path / "installations.json"
    store1 = InstallationStore(storage_path=store_file)
    id1 = store1.register()

    # Re-instantiate store to simulate broker restart
    store2 = InstallationStore(storage_path=store_file)
    assert store2.authenticate(id1.installation_id, id1.credential) is True
    assert store2.authenticate(id1.installation_id, "bad_secret") is False


def test_revocation_requires_credential_and_removes_registration(tmp_path):
    store_file = tmp_path / "installations.json"
    store = InstallationStore(storage_path=store_file)
    identity = store.register()

    # Revoke with wrong credential fails
    assert store.revoke(identity.installation_id, "wrong_secret") is False
    assert store.authenticate(identity.installation_id, identity.credential) is True

    # Revoke with valid credential succeeds
    assert store.revoke(identity.installation_id, identity.credential) is True
    assert store.authenticate(identity.installation_id, identity.credential) is False

    # Check removed from disk
    stored_data = json.loads(store_file.read_text())
    assert identity.installation_id not in stored_data
