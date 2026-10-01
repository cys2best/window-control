import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from server.app import AccessGate, create_app
from server.pairing import PairingStore

LOOPBACK = ("127.0.0.1", 5000)
LAN = ("192.168.1.50", 5000)
TAILNET = ("100.101.102.103", 5000)
PUBLIC = ("203.0.113.9", 5000)


def _make(peer, pairing=None, instances=None, **client_kwargs):
    manager = MagicMock()
    manager.list_instances.return_value = instances or []
    manager.active = None
    pairing = pairing or PairingStore()
    with patch("server.app.get_best_ip", return_value="127.0.0.1"):
        app = create_app(manager, pairing)
    return TestClient(app, client=peer, **client_kwargs), pairing


def _pair(client, pairing, name="Phone"):
    response = client.post("/pair", json={"code": pairing.start_pairing(), "device_name": name})
    assert response.status_code == 200
    return response.json()["token"]


# -- network gate ----------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("get", "/"), ("get", "/instances"), ("get", "/pair/status"),
    ("get", "/pair"), ("get", "/does-not-exist"),
])
def test_public_peer_is_refused_everywhere(method, path):
    client, _ = _make(PUBLIC)
    assert getattr(client, method)(path).status_code == 403


def test_public_peer_cannot_pair_even_with_the_right_code():
    client, pairing = _make(PUBLIC)
    response = client.post("/pair", json={"code": pairing.start_pairing(), "device_name": "x"})
    assert response.status_code == 403
    assert pairing.list_devices() == []


def test_default_testclient_peer_is_refused():
    manager = MagicMock()
    with patch("server.app.get_best_ip", return_value="127.0.0.1"):
        app = create_app(manager, PairingStore())
    assert TestClient(app).get("/pair/status").status_code == 403


def test_forwarded_for_header_cannot_change_the_peer():
    public, _ = _make(PUBLIC)
    assert public.get("/instances", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 403
    lan, _ = _make(LAN)
    assert lan.get("/instances", headers={"X-Forwarded-For": "127.0.0.1"}).status_code == 401


# -- pairing gate ----------------------------------------------------------

@pytest.mark.parametrize("peer", [LAN, TAILNET])
@pytest.mark.parametrize("method,path", [
    ("get", "/instances"),
    ("get", "/windows"),
    ("post", "/instances/emulator-5554/select"),
    ("post", "/instances/emulator-5554/keyframe"),
    ("get", "/instances/emulator-5554/preview"),
    ("get", "/preview"),
])
def test_unpaired_private_peer_gets_401_on_api_routes(peer, method, path):
    client, _ = _make(peer)
    response = getattr(client, method)(path)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_unpaired_private_peer_can_load_the_pairing_shell(tmp_path):
    import server.app as app_module
    (tmp_path / "pair.html").write_text("<html>pair shell</html>")
    (tmp_path / "index.html").write_text("<html>index shell</html>")
    (tmp_path / "pair.txt").write_bytes(b"0:payload\n")
    (tmp_path / "manifest.json").write_text("{}")
    (tmp_path / "_next" / "static").mkdir(parents=True)
    (tmp_path / "_next" / "static" / "a.js").write_text("console.log(1)")
    with patch.object(app_module, "WEB_BUILD_DIR", str(tmp_path)):
        client, _ = _make(LAN)
        assert client.get("/pair").text == "<html>pair shell</html>"
        assert client.get("/").text == "<html>index shell</html>"
        assert client.get("/pair.txt").status_code == 200
        assert client.get("/manifest.json").status_code == 200
        assert client.get("/_next/static/a.js").status_code == 200


def test_unknown_paths_stay_404_for_an_unpaired_peer():
    client, _ = _make(LAN)
    assert client.get("/does-not-exist").status_code == 404
    assert client.get("/login").status_code == 404
    assert client.get("/auth/config").status_code == 404


def test_loopback_needs_no_token():
    client, _ = _make(LOOPBACK, instances=[{"id": "adb:a", "serial": "a"}])
    assert client.get("/instances").json() == [{"id": "adb:a", "serial": "a"}]
    assert client.get("/pair/status").json() == {"paired": True}


# -- pairing flow ----------------------------------------------------------

def test_pairing_issues_a_token_that_opens_the_api():
    client, pairing = _make(LAN, instances=[{"id": "adb:a", "serial": "a"}])
    assert client.get("/pair/status").json() == {"paired": False}
    token = _pair(client, pairing)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/instances", headers=headers).status_code == 200
    assert client.get(f"/instances?token={token}").status_code == 200
    assert client.get("/pair/status", headers=headers).json() == {"paired": True}
    assert [d.name for d in pairing.list_devices()] == ["Phone"]


def test_wrong_code_and_no_active_code_are_indistinguishable():
    client, pairing = _make(LAN)
    closed = client.post("/pair", json={"code": "123456", "device_name": "x"})
    code = pairing.start_pairing()
    wrong = "000000" if code != "000000" else "111111"
    opened = client.post("/pair", json={"code": wrong, "device_name": "x"})
    assert closed.status_code == opened.status_code == 403
    assert closed.json() == opened.json() == {"detail": "Invalid or expired pairing code"}


def test_a_code_cannot_be_used_twice():
    client, pairing = _make(LAN)
    code = pairing.start_pairing()
    assert client.post("/pair", json={"code": code, "device_name": "a"}).status_code == 200
    assert client.post("/pair", json={"code": code, "device_name": "b"}).status_code == 403


@pytest.mark.parametrize("body", [{}, {"device_name": "x"}, {"code": 123456}, {"code": None}])
def test_malformed_pair_bodies_are_rejected_without_pairing(body):
    client, pairing = _make(LAN)
    pairing.start_pairing()
    assert client.post("/pair", json=body).status_code in (403, 422)
    assert pairing.list_devices() == []


def test_removed_device_is_rejected():
    client, pairing = _make(LAN)
    token = _pair(client, pairing)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/instances", headers=headers).status_code == 200
    pairing.remove_device(pairing.list_devices()[0].id)
    assert client.get("/instances", headers=headers).status_code == 401
    assert client.get("/pair/status", headers=headers).json() == {"paired": False}


def test_garbage_tokens_are_rejected():
    client, _ = _make(LAN)
    for value in ("Bearer nope", "Basic abc", "Bearer ", "nope"):
        assert client.get("/instances", headers={"Authorization": value}).status_code == 401
    assert client.get("/instances?token=nope").status_code == 401


# -- websocket scopes ------------------------------------------------------

def _run_gate(scope, pairing):
    sent, reached = [], []

    async def inner(scope, receive, send):
        reached.append(True)

    async def send(message):
        sent.append(message)

    asyncio.run(AccessGate(inner, pairing)(scope, None, send))
    return sent, reached


def _ws_scope(peer, query=b""):
    return {"type": "websocket", "client": peer, "path": "/ws",
            "headers": [], "query_string": query}


def test_websocket_from_a_public_peer_is_closed_before_accept():
    sent, reached = _run_gate(_ws_scope(PUBLIC), PairingStore())
    assert sent == [{"type": "websocket.close", "code": 1008}]
    assert reached == []


def test_websocket_from_an_unpaired_private_peer_is_closed():
    sent, reached = _run_gate(_ws_scope(LAN), PairingStore())
    assert sent == [{"type": "websocket.close", "code": 1008}]
    assert reached == []


def test_websocket_with_a_valid_token_reaches_the_app():
    pairing = PairingStore()
    token = pairing.pair(pairing.start_pairing(), "Phone")
    sent, reached = _run_gate(_ws_scope(LAN, f"token={token}".encode()), pairing)
    assert sent == [] and reached == [True]


def test_non_network_scopes_pass_through():
    sent, reached = _run_gate({"type": "lifespan"}, PairingStore())
    assert sent == [] and reached == [True]
