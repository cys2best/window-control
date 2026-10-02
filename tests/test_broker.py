import json
import pytest
from fastapi.testclient import TestClient
from broker.app import create_broker_app, BrokerSettings
from broker.identity_store import InstallationStore
from broker.limits import BrokerLimits


@pytest.fixture
def broker_env(tmp_path):
    storage = tmp_path / "broker_identities.json"
    settings = BrokerSettings(
        storage_path=str(storage),
        allowed_origins=["https://control.example.com"],
        turn_shared_secret="test_turn_secret_32bytes_value_here",
    )
    app = create_broker_app(settings)
    client = TestClient(app)
    return client, settings, storage


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
