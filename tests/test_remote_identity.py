import json
import os

import pytest

from broker.identity_store import InstallationIdentity
from server.pairing import PairingStore


class Protector:
    def protect(self, data):
        return b"protected:" + data[::-1]

    def unprotect(self, data):
        if not data.startswith(b"protected:"):
            raise ValueError("cannot decrypt")
        return data[10:][::-1]


class Service:
    def __init__(self):
        self.calls = []
        self.fail_delete = False

    def __call__(self, method, url, payload, timeout):
        self.calls.append((method, url, payload, timeout))
        if method == "DELETE":
            if self.fail_delete:
                raise OSError("offline")
            return {"ok": True}
        return {"installation_id": f"installation-{len(self.calls)}", "credential": "secret-owner-credential"}


def make_store(path, service=None, protector=None, **kwargs):
    from server.remote_identity import RemoteIdentityStore
    return RemoteIdentityStore(path, protector=protector or Protector(), request=service or Service(), **kwargs)


def test_identity_survives_restart_protected_and_owner_only(tmp_path):
    path = tmp_path / "nested" / "remote_identity.json"
    service = Service()
    first = make_store(path, service).load_or_register("https://remote.example")
    assert isinstance(first, InstallationIdentity)
    assert first.credential not in path.read_text()
    assert make_store(path, service).load_or_register("https://remote.example") == first
    assert len(service.calls) == 1
    assert 0 < service.calls[0][3] <= 10
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600


def test_cannot_decrypt_identity_preserves_local_access(tmp_path):
    from server.remote_identity import RemoteIdentityError
    path = tmp_path / "identity.json"
    path.write_text(json.dumps({"protected": "bm90IHByb3RlY3RlZA=="}))
    original = path.read_bytes()
    service = Service()
    local = PairingStore()
    with pytest.raises(RemoteIdentityError):
        make_store(path, service).load_or_register("https://remote.example")
    assert service.calls == []
    assert path.read_bytes() == original
    assert local.is_valid_token(local.pair(local.start_pairing(), "Phone"))


def test_unwritable_identity_does_not_fake_persistence(tmp_path):
    from server.remote_identity import RemoteIdentityError
    blocker = tmp_path / "blocker"
    blocker.write_text("file")
    with pytest.raises(RemoteIdentityError):
        make_store(blocker / "identity.json").load_or_register("https://remote.example")


def test_reset_requires_online_authenticated_deletion_before_replacement(tmp_path):
    from server.remote_identity import RemoteIdentityError
    path = tmp_path / "identity.json"
    service = Service()
    store = make_store(path, service)
    old = store.load_or_register("https://remote.example")
    original = path.read_bytes()
    service.fail_delete = True
    with pytest.raises(RemoteIdentityError):
        store.reset_identity()
    assert path.read_bytes() == original
    assert len(service.calls) == 2
    assert service.calls[-1][:3] == ("DELETE", "https://remote.example/installations/installation-1", {"credential": old.credential})
    service.fail_delete = False
    new = store.reset_identity()
    assert new.installation_id != old.installation_id
    assert [call[0] for call in service.calls] == ["POST", "DELETE", "DELETE", "POST"]
    assert make_store(path).load_or_register("https://remote.example") == new


@pytest.mark.parametrize("url", ["http://remote.example", "https://owner:secret@remote.example", "https://remote.example/?token=secret", "https://remote.example/#secret"])
def test_registration_rejects_insecure_or_credential_urls(tmp_path, url):
    from server.remote_identity import RemoteIdentityError
    service = Service()
    with pytest.raises(RemoteIdentityError):
        make_store(tmp_path / "identity.json", service).load_or_register(url)
    assert service.calls == []


def test_only_explicit_localhost_test_mode_allows_http(tmp_path):
    from server.remote_identity import RemoteIdentityError
    with pytest.raises(RemoteIdentityError):
        make_store(tmp_path / "a.json").load_or_register("http://localhost:8000")
    store = make_store(tmp_path / "b.json", allow_insecure_localhost=True)
    assert store.load_or_register("http://localhost:8000").installation_id
    with pytest.raises(RemoteIdentityError):
        store.load_or_register("http://remote.example")


def test_identity_cannot_silently_move_to_a_different_service(tmp_path):
    from server.remote_identity import RemoteIdentityError
    path = tmp_path / "identity.json"
    make_store(path).load_or_register("https://remote.example")
    service = Service()
    with pytest.raises(RemoteIdentityError):
        make_store(path, service).load_or_register("https://other.example")
    assert service.calls == []


def test_registration_failure_preserves_local_pairing(tmp_path):
    from server.remote_identity import RemoteIdentityError
    def offline(*args):
        raise OSError("offline")
    local = PairingStore()
    code = local.start_pairing()
    path = tmp_path / "identity.json"
    with pytest.raises(RemoteIdentityError):
        make_store(path, offline).load_or_register("https://remote.example")
    assert not path.exists()
    assert local.pair(code, "Phone")


def test_failed_atomic_write_reports_error_and_cleans_temporary_file(tmp_path, monkeypatch):
    from server.remote_identity import RemoteIdentityError
    def denied(*args):
        raise PermissionError("denied")
    monkeypatch.setattr("server.remote_identity.os.replace", denied)
    path = tmp_path / "identity.json"
    with pytest.raises(RemoteIdentityError):
        make_store(path).load_or_register("https://remote.example")
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []
