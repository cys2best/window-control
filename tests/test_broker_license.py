import hashlib
import json
import re

import pytest

from broker.license_store import (LicenseStore, LicenseStoreError, normalize_key,
                                  generate_key, MOVE_COOLDOWN_SECONDS)

A, B = "a" * 32, "b" * 32
alive = lambda _id: True


def test_generated_key_normalizes_from_sloppy_input():
    key = generate_key()
    assert re.fullmatch(r"EMU(-[0-9A-HJKMNP-TV-Z]{5}){4}", key)
    sloppy = " " + key.lower().replace("0", "o").replace("1", "l") + " "
    assert normalize_key(sloppy) == normalize_key(key)
    assert len(normalize_key(key)) == 20
    with pytest.raises(ValueError):
        normalize_key("EMU-ABCDE")
    with pytest.raises(ValueError):
        normalize_key("EMU-UUUUU-UUUUU-UUUUU-UUUUU")


def test_first_activation_binds_without_cooldown(tmp_path):
    store = LicenseStore(tmp_path / "licenses.json")
    key = store.issue(now=1000)
    assert store.activate(key, A, now=1000, installation_exists=alive).status == "bound"
    assert store.is_licensed(A, now=1001) and not store.is_licensed(B, now=1001)
    moved = store.activate(key, B, now=1002, installation_exists=alive)
    assert (moved.status, moved.previous_installation) == ("moved", A)
    assert store.is_licensed(B, now=1003) and not store.is_licensed(A, now=1003)


def test_second_move_within_cooldown_is_refused(tmp_path):
    store = LicenseStore(tmp_path / "licenses.json")
    key = store.issue(now=0)
    store.activate(key, A, now=0, installation_exists=alive)
    store.activate(key, B, now=10, installation_exists=alive)
    refused = store.activate(key, A, now=20, installation_exists=alive)
    assert (refused.status, refused.retry_after) == ("in_use", MOVE_COOLDOWN_SECONDS - 10)
    assert store.is_licensed(B, now=20)
    assert store.activate(key, A, now=10 + MOVE_COOLDOWN_SECONDS, installation_exists=alive).status == "moved"


def test_reactivating_same_installation_is_idempotent(tmp_path):
    store = LicenseStore(tmp_path / "licenses.json")
    key = store.issue(now=0)
    store.activate(key, A, now=0, installation_exists=alive)
    store.activate(key, B, now=10, installation_exists=alive)
    again = store.activate(key, B, now=20, installation_exists=alive)
    assert again.status == "bound"
    assert store.list()[0].moved_at == 10
    assert store.is_licensed(B, now=21)


def test_binding_to_deleted_installation_rebinds_without_cooldown(tmp_path):
    store = LicenseStore(tmp_path / "licenses.json")
    key = store.issue(now=0)
    store.activate(key, A, now=0, installation_exists=alive)
    store.activate(key, B, now=10, installation_exists=alive)
    result = store.activate(key, A, now=11, installation_exists=lambda i: i != B)
    assert result.status == "bound"
    assert store.is_licensed(A, now=12)


def test_deactivate_clears_binding_and_cooldown(tmp_path):
    store = LicenseStore(tmp_path / "licenses.json")
    key = store.issue(now=0)
    store.activate(key, A, now=0, installation_exists=alive)
    store.activate(key, B, now=10, installation_exists=alive)
    assert store.deactivate(B) is True
    assert not store.is_licensed(B, now=11)
    assert store.deactivate(B) is False
    assert store.activate(key, A, now=12, installation_exists=alive).status == "bound"


def test_unknown_revoked_and_expired_keys_are_invalid(tmp_path):
    store = LicenseStore(tmp_path / "licenses.json")
    assert store.activate(generate_key(), A, now=0, installation_exists=alive).status == "invalid"
    assert store.activate("EMU-ABCDE", A, now=0, installation_exists=alive).status == "invalid"
    revoked = store.issue(now=0)
    store.activate(revoked, A, now=0, installation_exists=alive)
    assert store.revoke(revoked) is True
    assert not store.is_licensed(A, now=1)
    assert store.activate(revoked, A, now=1, installation_exists=alive).status == "invalid"
    expiring = store.issue(now=0, days=1)
    assert store.activate(expiring, B, now=100, installation_exists=alive).status == "bound"
    assert store.is_licensed(B, now=86399)
    assert store.is_licensed(B, now=86400) is False
    assert store.activate(expiring, B, now=86400, installation_exists=alive).status == "invalid"


def test_reset_unbinds_without_cooldown(tmp_path):
    store = LicenseStore(tmp_path / "licenses.json")
    key = store.issue(now=0)
    store.activate(key, A, now=0, installation_exists=alive)
    store.activate(key, B, now=10, installation_exists=alive)
    assert store.reset(key) is True
    assert not store.is_licensed(B, now=11)
    assert store.activate(key, A, now=12, installation_exists=alive).status == "bound"
    assert store.reset(generate_key()) is False
    assert store.revoke(generate_key()) is False


def test_file_holds_digests_only(tmp_path):
    store = LicenseStore(tmp_path / "licenses.json")
    key = store.issue(now=0, note="friend")
    text = (tmp_path / "licenses.json").read_text()
    assert normalize_key(key) not in text and hashlib.sha256(normalize_key(key).encode()).hexdigest() in text
    record = next(iter(json.loads(text).values()))
    assert set(record) == {"installation_id", "issued_at", "bound_at", "moved_at",
                           "expires_at", "revoked", "note"}
    view = store.list()[0]
    assert view.digest_prefix == hashlib.sha256(normalize_key(key).encode()).hexdigest()[:8]
    assert view.note == "friend" and view.installation_id is None


def test_corrupt_license_file_fails_closed(tmp_path):
    path = tmp_path / "licenses.json"
    path.write_text("{not json")
    store = LicenseStore(path)
    assert store.is_licensed(A, now=0) is False
    with pytest.raises(LicenseStoreError):
        store.activate(generate_key(), A, now=0, installation_exists=alive)
    with pytest.raises(LicenseStoreError):
        store.issue(now=0)
    assert path.read_text() == "{not json"


def test_invalid_record_shape_fails_closed(tmp_path):
    path = tmp_path / "licenses.json"
    path.write_text(json.dumps({"0" * 64: {"installation_id": A}}))
    store = LicenseStore(path)
    assert store.is_licensed(A, now=0) is False
    with pytest.raises(LicenseStoreError):
        store.list()


def test_second_store_instance_sees_admin_changes(tmp_path):
    path = tmp_path / "licenses.json"
    broker = LicenseStore(path)
    admin = LicenseStore(path)
    key = admin.issue(now=0)
    assert broker.activate(key, A, now=0, installation_exists=alive).status == "bound"
    assert broker.is_licensed(A, now=1)
    admin.revoke(key)
    assert broker.is_licensed(A, now=2) is False


# --- Broker HTTP endpoints and relay admission ---------------------------------

import uuid

from tests.test_broker import broker_env, wire, authenticate_host, approve_viewer  # noqa: F401


def register(client):
    return client.post("/installations").json()


def activate(client, identity, key):
    return client.post(f"/installations/{identity['installation_id']}/license",
                       json={"credential": identity["credential"], "key": key})


def select_bundle(host, viewer, generation=1):
    viewer.send_text(wire("select", {"serial": "emulator-5554"}))
    routed = host.receive_json()
    session_id = str(uuid.uuid4())
    host.send_text(wire("media_authorize", {"routing_id": routed["id"], "session_id": session_id,
                                            "generation": generation}))
    reply = host.receive_json()
    assert reply["ok"], reply
    host.send_json({"v": 1, "id": routed["id"], "ok": False,
                    "error": {"code": "busy", "message": "test ends here"}})
    viewer.receive_json()
    assert host.receive_json()["op"] == "media_cancel"
    return reply["result"]["viewer"]


def turn_servers(bundle):
    return [s for s in bundle["ice_servers"] if any(u.startswith(("turn:", "turns:")) for u in s["urls"])]


def test_unlicensed_installation_gets_stun_only_and_licensed_gets_turn(broker_env):
    client, _, _ = broker_env
    identity = register(client)
    with client.websocket_connect("/connect") as host, client.websocket_connect("/connect") as viewer:
        authenticate_host(host, identity)
        approve_viewer(host, viewer, identity)
        bundle = select_bundle(host, viewer)
        assert turn_servers(bundle) == [] and bundle["relay_available"] is False
        key = client.app.state.licenses.issue(now=0)
        response = activate(client, identity, key)
        assert (response.status_code, response.json()) == (200, {"relay_licensed": True})
        bundle = select_bundle(host, viewer, generation=2)
        assert bundle["relay_available"] is True
        assert identity["installation_id"] in turn_servers(bundle)[0]["username"]


def test_license_endpoint_requires_installation_credential(broker_env):
    client, _, _ = broker_env
    identity = register(client)
    key = client.app.state.licenses.issue(now=0)
    response = activate(client, {**identity, "credential": "wrong"}, key)
    assert (response.status_code, response.json()) == (401, {"detail": "Unauthorized"})
    response = client.request("DELETE", f"/installations/{identity['installation_id']}/license",
                              json={"credential": "wrong"})
    assert response.status_code == 401
    assert client.app.state.licenses.list()[0].installation_id is None


def test_invalid_and_malformed_keys_return_same_403(broker_env):
    client, _, _ = broker_env
    identity = register(client)
    for key in ("EMU-AAAAA", generate_key()):
        response = activate(client, identity, key)
        assert (response.status_code, response.json()) == (403, {"detail": "invalid_license"})


def test_move_revokes_previous_installation_and_its_viewers(broker_env):
    client, _, _ = broker_env
    first, second = register(client), register(client)
    store = client.app.state.licenses
    key = store.issue(now=0)
    with client.websocket_connect("/connect") as host, client.websocket_connect("/connect") as viewer:
        authenticate_host(host, first)
        approve_viewer(host, viewer, first)
        assert activate(client, first, key).status_code == 200
        response = activate(client, second, key)
        assert (response.status_code, response.json()) == (200, {"relay_licensed": True})
        assert client.app.state.registry.get_host(first["installation_id"]) is None
        viewer.send_text(wire("instances"))
        assert viewer.receive_json()["error"]["code"] == "not_paired"
        host.send_text(wire("pairing_close", {"handle": "x" * 32}))
        assert host.receive_json()["error"]["code"] == "stale_generation"
    response = client.post("/installations/" + first["installation_id"] + "/license",
                           json={"credential": first["credential"], "key": key})
    assert response.status_code == 401
    assert store.is_licensed(second["installation_id"], now=1)


def test_move_within_cooldown_returns_retry_after(broker_env, monkeypatch):
    client, _, _ = broker_env
    now = [1000.0]
    monkeypatch.setattr("broker.app.time.time", lambda: now[0])
    first, second, third = register(client), register(client), register(client)
    key = client.app.state.licenses.issue(now=now[0])
    assert activate(client, first, key).status_code == 200
    assert activate(client, second, key).status_code == 200
    now[0] += 100
    response = activate(client, third, key)
    assert (response.status_code, response.json()) == (
        409, {"detail": "license_in_use", "retry_after": MOVE_COOLDOWN_SECONDS - 100})
    assert client.app.state.licenses.is_licensed(second["installation_id"], now=now[0])


def test_deactivate_turns_relay_off(broker_env):
    client, _, _ = broker_env
    identity = register(client)
    key = client.app.state.licenses.issue(now=0)
    assert activate(client, identity, key).status_code == 200
    response = client.request("DELETE", f"/installations/{identity['installation_id']}/license",
                              json={"credential": identity["credential"]})
    assert (response.status_code, response.json()) == (200, {"relay_licensed": False})
    assert not client.app.state.licenses.is_licensed(identity["installation_id"], now=1)
    with client.websocket_connect("/connect") as host, client.websocket_connect("/connect") as viewer:
        authenticate_host(host, identity)
        approve_viewer(host, viewer, identity)
        assert select_bundle(host, viewer)["relay_available"] is False


def test_host_auth_reports_license_status(broker_env):
    client, _, _ = broker_env
    identity = register(client)

    def auth_result():
        with client.websocket_connect("/connect") as host:
            host.send_text(wire("host_auth", identity))
            return host.receive_json()["result"]

    assert auth_result()["relay_licensed"] is False
    activate(client, identity, client.app.state.licenses.issue(now=0))
    assert auth_result()["relay_licensed"] is True


def test_revoked_license_stops_relay_on_next_admission(broker_env):
    client, _, _ = broker_env
    identity = register(client)
    store = client.app.state.licenses
    key = store.issue(now=0)
    activate(client, identity, key)
    with client.websocket_connect("/connect") as host, client.websocket_connect("/connect") as viewer:
        authenticate_host(host, identity)
        approve_viewer(host, viewer, identity)
        assert select_bundle(host, viewer)["relay_available"] is True
        store.revoke(key)
        bundle = select_bundle(host, viewer, generation=2)
        assert bundle["relay_available"] is False and turn_servers(bundle) == []


def test_corrupt_license_file_returns_503_and_stun_only(broker_env):
    client, settings, storage = broker_env
    identity = register(client)
    license_path = storage.with_name("licenses.json")
    license_path.write_text("{bad")
    response = activate(client, identity, generate_key())
    assert (response.status_code, response.json()) == (503, {"detail": "License storage unavailable"})
    with client.websocket_connect("/connect") as host, client.websocket_connect("/connect") as viewer:
        authenticate_host(host, identity)
        approve_viewer(host, viewer, identity)
        assert select_bundle(host, viewer)["relay_available"] is False
    assert license_path.read_text() == "{bad"
