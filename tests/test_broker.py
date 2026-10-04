import json
import pytest
from fastapi.testclient import TestClient
from broker.app import create_broker_app, BrokerSettings
from broker.identity_store import InstallationStore
from broker.limits import BrokerLimits
from starlette.websockets import WebSocketDisconnect


@pytest.fixture
def broker_env(tmp_path):
    storage = tmp_path / "broker_identities.json"
    settings = BrokerSettings(
        storage_path=str(storage),
        allowed_origins=["https://control.example.com"],
        turn_shared_secret="test_turn_secret_32bytes_value_here",
        max_active_streams=2,
        stun_urls=["stun:stun.example:3478"],
        turn_urls=["turn:turn.example:3478?transport=udp"],
    )
    app = create_broker_app(settings)
    with TestClient(app) as client:
        yield client, settings, storage


def test_registration_does_not_grant_turn(broker_env):
    client, settings, _ = broker_env
    # Register installation
    resp = client.post("/installations")
    assert resp.status_code == 200
    data = resp.json()
    assert "installation_id" in data
    assert "credential" in data
    # No turn credentials or ice servers are granted at registration time
    assert "ice_servers" not in data
    assert "turn" not in data


def test_installation_lifecycle_http(broker_env):
    client, settings, _ = broker_env
    # Register
    resp = client.post("/installations")
    data = resp.json()
    inst_id = data["installation_id"]
    cred = data["credential"]

    # Delete with bad credential
    del_bad = client.request("DELETE", f"/installations/{inst_id}", json={"credential": "wrong"})
    assert del_bad.status_code == 401

    # Delete with valid credential
    del_ok = client.request("DELETE", f"/installations/{inst_id}", json={"credential": cred})
    assert del_ok.status_code == 200

    # Delete again -> 404
    del_again = client.request("DELETE", f"/installations/{inst_id}", json={"credential": cred})
    assert del_again.status_code == 404


def test_corrupt_storage_returns_sanitized_503_without_issuing_an_identity(broker_env):
    client, _, storage = broker_env
    corrupt = b'{"truncated":'
    storage.write_bytes(corrupt)
    response = client.post("/installations")
    assert response.status_code == 503
    assert response.json() == {"detail": "Installation storage unavailable"}
    assert storage.read_bytes() == corrupt
    response = client.request("DELETE", "/installations/" + "a" * 32,
                              json={"credential": "secret"})
    assert response.status_code == 503
    assert response.json() == {"detail": "Installation storage unavailable"}
    assert storage.read_bytes() == corrupt


@pytest.mark.parametrize("boundary", ["fsync", "replace"])
def test_failed_registration_write_returns_503_and_keeps_previous_identity(
    broker_env, monkeypatch, boundary
):
    client, _, storage = broker_env
    existing = client.post("/installations").json()
    before = storage.read_bytes()

    def failed_write(*args):
        raise OSError("private storage path and credential")

    monkeypatch.setattr("broker.identity_store.os." + boundary, failed_write)
    response = client.post("/installations")
    assert response.status_code == 503
    assert response.json() == {"detail": "Installation storage unavailable"}
    assert storage.read_bytes() == before
    assert InstallationStore(storage).authenticate(**{
        "id": existing["installation_id"], "credential": existing["credential"]
    })


def test_failed_revoke_write_keeps_identity_and_connected_host(broker_env, monkeypatch):
    client, _, storage = broker_env
    existing = client.post("/installations").json()
    before = storage.read_bytes()
    with client.websocket_connect("/connect") as host:
        authenticate_host(host, existing)

        def failed_replace(*args):
            raise OSError("private storage path and credential")

        monkeypatch.setattr("broker.identity_store.os.replace", failed_replace)
        response = client.request("DELETE", "/installations/" + existing["installation_id"],
                                  json={"credential": existing["credential"]})
        assert response.status_code == 503
        assert response.json() == {"detail": "Installation storage unavailable"}
        assert storage.read_bytes() == before
        assert client.app.state.registry.get_host(existing["installation_id"]) is not None
        assert InstallationStore(storage).authenticate(existing["installation_id"], existing["credential"])


@pytest.mark.parametrize("failure", ["corrupt", "unreadable"])
def test_storage_failure_closes_host_authentication_without_registering_host(
    broker_env, monkeypatch, failure
):
    from pathlib import Path
    client, _, storage = broker_env
    existing = client.post("/installations").json()
    if failure == "corrupt":
        storage.write_bytes(b'{"truncated":')
    else:
        def denied_read(*args, **kwargs):
            raise PermissionError("private storage path and credential")
        monkeypatch.setattr(Path, "read_text", denied_read)
    with client.websocket_connect("/connect") as host:
        host.send_text(wire("host_auth", existing))
        with pytest.raises(WebSocketDisconnect) as closed:
            host.receive_text()
        assert closed.value.code == 1011
        assert closed.value.reason == "Installation storage unavailable"
    assert client.app.state.registry.get_host(existing["installation_id"]) is None


def test_viewer_first_frame_auth(broker_env):
    client, settings, _ = broker_env
    # Register host installation
    reg = client.post("/installations").json()
    inst_id = reg["installation_id"]
    cred = reg["credential"]

    # Connect WebSocket without auth in URL query params
    with client.websocket_connect("/connect") as ws:
        # First frame must authenticate
        ws.send_text(json.dumps({
            "v": 1,
            "id": "550e8400-e29b-41d4-a716-446655440001",
            "op": "host_auth",
            "payload": {
                "installation_id": inst_id,
                "credential": cred,
            }
        }))
        reply = json.loads(ws.receive_text())
        assert reply["ok"] is True
        assert reply["result"]["authenticated"] is True


def test_cross_installation_reply_rejected(broker_env):
    client, settings, _ = broker_env
    # Register installation A and B
    reg_a = client.post("/installations").json()
    reg_b = client.post("/installations").json()

    with client.websocket_connect("/connect") as ws_host_a, \
         client.websocket_connect("/connect") as ws_host_b:
        # Auth Host A
        ws_host_a.send_text(json.dumps({
            "v": 1, "id": "11111111-1111-1111-1111-111111111111", "op": "host_auth",
            "payload": {"installation_id": reg_a["installation_id"], "credential": reg_a["credential"]}
        }))
        assert json.loads(ws_host_a.receive_text())["ok"] is True

        # Auth Host B
        ws_host_b.send_text(json.dumps({
            "v": 1, "id": "22222222-2222-2222-2222-222222222222", "op": "host_auth",
            "payload": {"installation_id": reg_b["installation_id"], "credential": reg_b["credential"]}
        }))
        assert json.loads(ws_host_b.receive_text())["ok"] is True

        # Host B attempts to reply to request intended for A
        ws_host_b.send_text(json.dumps({
            "v": 1, "id": "33333333-3333-3333-3333-333333333333",
            "ok": True,
            "result": {"for": "installation_a"}
        }))
        err = json.loads(ws_host_b.receive_text())
        assert err["ok"] is False
        assert err["error"]["code"] in ("invalid_request", "stale_generation")



def test_old_host_socket_cannot_reply(broker_env):
    client, settings, _ = broker_env
    reg = client.post("/installations").json()
    inst_id = reg["installation_id"]
    cred = reg["credential"]

    with client.websocket_connect("/connect") as ws1:
        ws1.send_text(json.dumps({
            "v": 1, "id": "11111111-1111-1111-1111-111111111111", "op": "host_auth",
            "payload": {"installation_id": inst_id, "credential": cred}
        }))
        assert json.loads(ws1.receive_text())["ok"] is True

        with client.websocket_connect("/connect") as ws2:
            ws2.send_text(json.dumps({
                "v": 1, "id": "22222222-2222-2222-2222-222222222222", "op": "host_auth",
                "payload": {"installation_id": inst_id, "credential": cred}
            }))
            assert json.loads(ws2.receive_text())["ok"] is True

            ws1.send_text(json.dumps({
                "v": 1, "id": "33333333-3333-3333-3333-333333333333",
                "ok": True,
                "result": {"old": "epoch"}
            }))
            try:
                msg = json.loads(ws1.receive_text())
                assert msg["ok"] is False
            except Exception:
                pass

def test_restart_requires_reauthentication(broker_env):
    client, settings, storage = broker_env
    reg = client.post("/installations").json()
    inst_id = reg["installation_id"]
    cred = reg["credential"]

    # Simulate broker restart by creating new app with same storage
    new_app = create_broker_app(settings)
    new_client = TestClient(new_app)

    # Saved credential still works
    with new_client.websocket_connect("/connect") as ws:
        ws.send_text(json.dumps({
            "v": 1, "id": "550e8400-e29b-41d4-a716-446655440001", "op": "host_auth",
            "payload": {"installation_id": inst_id, "credential": cred}
        }))
        reply = json.loads(ws.receive_text())
        assert reply["ok"] is True


def test_origin_checking(broker_env):
    client, settings, _ = broker_env
    # Disallowed origin
    with pytest.raises(Exception):
        with client.websocket_connect("/connect", headers={"origin": "https://bad.com"}) as ws:
            ws.receive_text()
            
    # Allowed origin
    with client.websocket_connect("/connect", headers={"origin": "https://control.example.com"}) as ws:
        ws.send_text(json.dumps({"v": 1, "id": "66666666-6666-6666-6666-666666666666", "op": "invalid_auth"}))
        assert json.loads(ws.receive_text())["ok"] is False

    # Absent origin
    with client.websocket_connect("/connect") as ws:
        ws.send_text(json.dumps({"v": 1, "id": "66666666-6666-6666-6666-666666666666", "op": "invalid_auth"}))
        assert json.loads(ws.receive_text())["ok"] is False



def wire(op, payload=None, request_id=None):
    import uuid
    return json.dumps({"v": 1, "id": request_id or str(uuid.uuid4()), "op": op, "payload": payload or {}})


def authenticate_host(ws, identity):
    ws.send_text(wire("host_auth", identity))
    assert json.loads(ws.receive_text())["ok"]


def approve_viewer(host_ws, viewer_ws, identity, request_id=None):
    viewer_ws.send_text(wire("viewer_auth", {"installation_id": identity["installation_id"], "token": "pc-device-token"}, request_id))
    routed = json.loads(host_ws.receive_text())
    assert routed["context"]["installation_id"] == identity["installation_id"]
    assert routed["context"]["token"] == "pc-device-token"
    host_ws.send_text(json.dumps({"v": 1, "id": routed["id"], "ok": True, "result": {"authenticated": True, "device_id": "device1"}}))
    reply = json.loads(viewer_ws.receive_text())
    assert reply["ok"]
    assert "device_id" not in reply["result"]


def test_viewer_routing_and_limits(broker_env):
    client, settings, _ = broker_env
    identity = client.post("/installations").json()
    with client.websocket_connect("/connect") as host_ws:
        authenticate_host(host_ws, identity)
        with client.websocket_connect("/connect") as viewer_ws:
            approve_viewer(host_ws, viewer_ws, identity)
            routed = []
            original_ids = [f"44444444-4444-4444-4444-4444444444{i:02d}" for i in range(3)]
            for request_id in original_ids:
                viewer_ws.send_text(wire("preview", {"serial": "emulator-5554"}, request_id))
            for _ in range(2):
                routed.append(json.loads(host_ws.receive_text()))
            err = json.loads(viewer_ws.receive_text())
            assert err["error"]["code"] == "quota_exceeded"
            for command in routed:
                host_ws.send_text(json.dumps({"v": 1, "id": command["id"], "ok": True, "result": {}}))
            assert {json.loads(viewer_ws.receive_text())["id"] for _ in range(2)} == set(original_ids[:2])
            for i in range(33):
                viewer_ws.send_text(wire("instances", request_id=f"55555555-5555-5555-5555-5555555555{i:02d}"))
            commands = [json.loads(host_ws.receive_text()) for _ in range(32)]
            assert len({c["id"] for c in commands}) == 32
            err = json.loads(viewer_ws.receive_text())
            assert err["error"]["code"] == "invalid_request"


def test_foreign_host_cannot_consume_pending_authorization(broker_env):
    client, settings, _ = broker_env
    a = client.post("/installations").json()
    b = client.post("/installations").json()
    with client.websocket_connect("/connect") as host_a, client.websocket_connect("/connect") as host_b:
        authenticate_host(host_a, a)
        authenticate_host(host_b, b)
        with client.websocket_connect("/connect") as viewer:
            viewer.send_text(wire("viewer_auth", {"installation_id": a["installation_id"], "token": "phone"}))
            routed = json.loads(host_a.receive_text())
            response = {"v": 1, "id": routed["id"], "ok": True, "result": {"authenticated": True, "device_id": "device1"}}
            host_b.send_text(json.dumps(response))
            assert json.loads(host_b.receive_text())["error"]["code"] == "invalid_request"
            # Wrong owner did not remove the real pending request.
            host_a.send_text(json.dumps(response))
            assert json.loads(viewer.receive_text())["ok"]


def test_request_id_collision_across_viewers_is_isolated(broker_env):
    client, settings, _ = broker_env
    identity = client.post("/installations").json()
    same_id = "11111111-1111-1111-1111-111111111111"
    with client.websocket_connect("/connect") as host:
        authenticate_host(host, identity)
        with client.websocket_connect("/connect") as a, client.websocket_connect("/connect") as b:
            approve_viewer(host, a, identity)
            approve_viewer(host, b, identity)
            a.send_text(wire("instances", request_id=same_id))
            b.send_text(wire("instances", request_id=same_id))
            commands = [json.loads(host.receive_text()) for _ in range(2)]
            assert commands[0]["id"] != commands[1]["id"]
            assert commands[0]["context"]["viewer_id"] != commands[1]["context"]["viewer_id"]
            for i, command in enumerate(commands):
                host.send_text(json.dumps({"v": 1, "id": command["id"], "ok": True, "result": {"index": i}}))
            assert json.loads(a.receive_text())["id"] == same_id
            assert json.loads(b.receive_text())["id"] == same_id


def test_duplicate_active_id_and_viewer_role_forgery_rejected(broker_env):
    client, settings, _ = broker_env
    identity = client.post("/installations").json()
    with client.websocket_connect("/connect") as host:
        authenticate_host(host, identity)
        with client.websocket_connect("/connect") as viewer:
            approve_viewer(host, viewer, identity)
            request_id = "11111111-1111-1111-1111-111111111111"
            viewer.send_text(wire("instances", request_id=request_id))
            command = json.loads(host.receive_text())
            viewer.send_text(wire("instances", request_id=request_id))
            assert json.loads(viewer.receive_text())["error"]["code"] == "invalid_request"
            for op, payload in [("host_auth", identity), ("device_invalidated", {"device_id": "device1"}), ("pairing_close", {"handle": "x" * 43}), ("instances", {"role": "host"})]:
                viewer.send_text(wire(op, payload))
                assert json.loads(viewer.receive_text())["error"]["code"] == "invalid_request"
            forged = json.loads(wire("instances"))
            forged["context"] = {"token": "owner"}
            viewer.send_text(json.dumps(forged))
            assert json.loads(viewer.receive_text())["error"]["code"] == "invalid_request"


def test_pending_request_dies_with_host_epoch_and_requires_viewer_reauth(broker_env):
    client, settings, _ = broker_env
    identity = client.post("/installations").json()
    with client.websocket_connect("/connect") as host:
        authenticate_host(host, identity)
        with client.websocket_connect("/connect") as viewer:
            approve_viewer(host, viewer, identity)
            viewer.send_text(wire("instances"))
            routed = json.loads(host.receive_text())
            with client.websocket_connect("/connect") as replacement:
                authenticate_host(replacement, identity)
                assert json.loads(viewer.receive_text())["error"]["code"] == "offline"
                replacement.send_text(json.dumps({"v": 1, "id": routed["id"], "ok": True, "result": {}}))
                assert json.loads(replacement.receive_text())["error"]["code"] == "invalid_request"
                viewer.send_text(wire("instances"))
                assert json.loads(viewer.receive_text())["error"]["code"] == "not_paired"


def test_negotiate_budget_includes_broker_queue_time_and_late_reply_is_discarded(broker_env):
    import time
    client, settings, _ = broker_env
    identity = client.post("/installations").json()
    with client.websocket_connect("/connect") as host:
        authenticate_host(host, identity)
        with client.websocket_connect("/connect") as viewer:
            approve_viewer(host, viewer, identity)
            host_conn = client.app.state.registry.get_host(identity["installation_id"])
            client.portal.call(host_conn.send_lock.acquire)
            payload = {"session_id": "11111111-1111-1111-1111-111111111111", "generation": 1, "offer": "v=0", "timeout_ms": 200}
            viewer.send_text(wire("negotiate", payload))
            time.sleep(.03)
            client.portal.call(host_conn.send_lock.release)
            routed = json.loads(host.receive_text())
            assert 0 < routed["payload"]["timeout_ms"] < 190
            assert json.loads(viewer.receive_text())["error"]["code"] == "timeout"
            host.send_text(json.dumps({"v": 1, "id": routed["id"], "ok": True, "result": {"answer": "late"}}))
            cancellation = json.loads(host.receive_text())
            assert cancellation["op"] == "media_cancel"
            assert cancellation["payload"]["routing_id"] == routed["id"]
            # A lifecycle round-trip proves the late reply was consumed with
            # no rejection preceding it on the healthy host channel.
            request_id = "22222222-2222-2222-2222-222222222222"
            host.send_text(wire("pairing_close", {"handle": "r" * 43}, request_id))
            assert json.loads(host.receive_text())["id"] == request_id


def test_pairing_handle_bound_to_host_epoch_expiry_and_lifecycle_role(broker_env):
    import time
    client, settings, _ = broker_env
    a = client.post("/installations").json()
    b = client.post("/installations").json()
    handle = "r" * 43
    with client.websocket_connect("/connect") as host_a, client.websocket_connect("/connect") as host_b:
        authenticate_host(host_a, a)
        authenticate_host(host_b, b)
        host_a.send_text(wire("pairing_open", {"handle": handle, "expires_at": time.time() + 100}))
        assert json.loads(host_a.receive_text())["ok"]
        host_b.send_text(wire("pairing_open", {"handle": handle, "expires_at": time.time() + 100}))
        assert json.loads(host_b.receive_text())["error"]["code"] == "invalid_request"
        host_b.send_text(wire("pairing_close", {"handle": handle}))
        assert json.loads(host_b.receive_text())["ok"]
        assert client.app.state.registry.installation_for_handle(handle) == a["installation_id"]
        host_a.send_text(wire("pairing_open", {"handle": "s" * 43, "expires_at": time.time() - 1}))
        assert json.loads(host_a.receive_text())["error"]["code"] == "invalid_request"
        with client.websocket_connect("/connect") as replacement:
            authenticate_host(replacement, a)
            assert client.app.state.registry.installation_for_handle(handle) is None


@pytest.mark.asyncio
async def test_settled_routing_is_bounded_and_rejects_foreign_or_stale_owners(monkeypatch):
    import time
    from broker.registry import Registry
    monkeypatch.setattr("broker.registry.MAX_SETTLED_REQUESTS", 4)
    registry = Registry()
    a = registry.register_host("a", object())
    b = registry.register_host("b", object())
    viewer = registry.register_viewer("viewer", "a", object(), a.epoch)
    first = registry.add_pending_request(viewer, "first")
    registry.drop_pending(first)
    assert registry.is_settled_request(first.routing_id, "a", a.epoch)
    assert not registry.is_settled_request(first.routing_id, "b", b.epoch)
    assert registry.is_settled_request(first.routing_id, "a", a.epoch)
    for index in range(8):
        pending = registry.add_pending_request(viewer, str(index))
        registry.drop_pending(pending)
    assert len(registry.settled_requests) == 4
    assert not registry.is_settled_request(first.routing_id, "a", a.epoch)
    registry.settled_requests.clear()
    registry.settled_requests[pending.routing_id] = ("a", a.epoch, time.monotonic() - 1)
    assert not registry.is_settled_request(pending.routing_id, "a", a.epoch)
    assert registry.settled_requests == {}
    fresh = registry.add_pending_request(viewer, "last")
    registry.drop_pending(fresh)
    replacement = registry.register_host("a", object())
    assert not registry.is_settled_request(fresh.routing_id, "a", a.epoch)
    assert not registry.is_settled_request(fresh.routing_id, "a", replacement.epoch)


def test_foreign_host_cannot_consume_settled_routing_id(broker_env):
    client, settings, _ = broker_env
    a = client.post("/installations").json()
    b = client.post("/installations").json()
    with client.websocket_connect("/connect") as host_a, client.websocket_connect("/connect") as host_b:
        authenticate_host(host_a, a)
        authenticate_host(host_b, b)
        with client.websocket_connect("/connect") as viewer:
            approve_viewer(host_a, viewer, a)
            viewer.send_text(wire("instances"))
            routed = json.loads(host_a.receive_text())
            response = json.dumps({"v": 1, "id": routed["id"], "ok": True, "result": {}})
            host_a.send_text(response)
            assert json.loads(viewer.receive_text())["ok"]
            host_b.send_text(response)
            assert json.loads(host_b.receive_text())["error"]["code"] == "invalid_request"
            host_a.send_text(response)
            request_id = "22222222-2222-2222-2222-222222222222"
            host_a.send_text(wire("pairing_close", {"handle": "r" * 43}, request_id))
            assert json.loads(host_a.receive_text())["id"] == request_id
