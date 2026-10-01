import asyncio
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from server.app import AccessGate, create_app
from server.pairing import PairingStore

LOOPBACK = ("127.0.0.1", 5000)
LOCAL_BASE = "http://127.0.0.1:8080"
LAN = ("192.168.1.50", 5000)
TAILNET = ("100.101.102.103", 5000)
PUBLIC = ("203.0.113.9", 5000)


def _make(peer, pairing=None, instances=None, **client_kwargs):
    manager = MagicMock()
    manager.list_instances.return_value = instances or []
    manager.active = None
    if pairing is None:
        pairing = PairingStore()
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
    client, _ = _make(LOOPBACK, instances=[{"id": "adb:a", "serial": "a"}], base_url=LOCAL_BASE)
    assert client.get("/instances").json() == [{"id": "adb:a", "serial": "a"}]
    assert client.get("/pair/status").json() == {"paired": True}


@pytest.mark.parametrize("host", ["127.0.0.1:8080", "localhost:8080", "LOCALHOST", "[::1]:8080", "127.0.0.1"])
def test_loopback_with_a_local_host_name_needs_no_token(host):
    client, _ = _make(LOOPBACK, instances=[{"id": "adb:a", "serial": "a"}])
    assert client.get("/instances", headers={"host": host}).status_code == 200
    assert client.get("/pair/status", headers={"host": host}).json() == {"paired": True}


@pytest.mark.parametrize("host", ["rebind.attacker.example:8080", "evil.localhost.example", "192.168.1.5:8080", "testserver"])
def test_loopback_with_a_foreign_host_is_not_trusted(host):
    client, _ = _make(LOOPBACK, instances=[{"id": "adb:a", "serial": "a"}])
    headers = {"host": host}
    assert client.get("/instances", headers=headers).status_code == 401
    assert client.get("/openapi.json", headers=headers).status_code in (401, 404)
    assert client.post("/instances/adb:a/select", headers=headers).status_code == 401
    assert client.get("/pair/status", headers=headers).json() == {"paired": False}


def test_loopback_with_a_foreign_host_still_works_with_a_token():
    client, pairing = _make(LOOPBACK, instances=[{"id": "adb:a", "serial": "a"}])
    token = _pair(client, pairing)
    response = client.get(
        "/instances", headers={"host": "rebind.attacker.example", "Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200


def test_loopback_cross_site_post_needs_a_token():
    client, _ = _make(LOOPBACK, base_url=LOCAL_BASE)
    assert client.post("/instances/x/keyframe", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 401
    assert client.post("/instances/x/keyframe", headers={"Sec-Fetch-Site": "same-origin"}).status_code != 401
    assert client.post("/instances/x/keyframe").status_code != 401
    # reads are not state-changing, so the header does not matter
    assert client.get("/instances", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


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


# -- html shell exemption (regression) ──────────────────────────────────────

def test_instances_html_shell_exemption_for_unpaired_lan_peer(tmp_path):
    import server.app as app_module
    (tmp_path / "instances.html").write_text("<html>instances shell</html>")
    with patch.object(app_module, "WEB_BUILD_DIR", str(tmp_path)):
        client, _ = _make(LAN, instances=[{"id": "adb:a", "serial": "a", "name": "test"}])
        # HTML preference → shell, not instance data (which would leak details to unpaired peer)
        response = client.get("/instances", headers={"Accept": "text/html"})
        assert response.status_code == 200
        assert response.text == "<html>instances shell</html>"


def test_instances_json_api_requires_pairing_for_unpaired_lan_peer(tmp_path):
    import server.app as app_module
    (tmp_path / "instances.html").write_text("<html>instances shell</html>")
    with patch.object(app_module, "WEB_BUILD_DIR", str(tmp_path)):
        client, _ = _make(LAN, instances=[{"id": "adb:a", "serial": "a"}])
        # JSON-like Accept headers → require pairing
        for headers in [{}, {"Accept": "*/*"}, {"Accept": "application/json"}]:
            response = client.get("/instances", headers=headers)
            assert response.status_code == 401, f"Failed for headers {headers}"
            assert response.headers["www-authenticate"] == "Bearer"


def test_account_html_shell_exemption_for_unpaired_lan_peer(tmp_path):
    import server.app as app_module
    (tmp_path / "account.html").write_text("<html>account shell</html>")
    with patch.object(app_module, "WEB_BUILD_DIR", str(tmp_path)):
        client, _ = _make(LAN)
        # HTML preference → shell
        response = client.get("/account", headers={"Accept": "text/html"})
        assert response.status_code == 200
        assert response.text == "<html>account shell</html>"


def test_account_json_api_requires_pairing_for_unpaired_lan_peer():
    client, _ = _make(LAN)
    # JSON-like Accept → require pairing
    response = client.get("/account", headers={"Accept": "application/json"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_loopback_and_paired_peers_get_json_without_html_header():
    client_loopback, _ = _make(LOOPBACK, instances=[{"id": "adb:a", "serial": "a"}], base_url=LOCAL_BASE)
    assert client_loopback.get("/instances").headers["content-type"].startswith("application/json")
    assert client_loopback.get("/instances", headers={"Accept": "*/*"}).headers["content-type"].startswith("application/json")

    client_lan, pairing = _make(LAN, instances=[{"id": "adb:a", "serial": "a"}])
    token = _pair(client_lan, pairing)
    headers = {"Authorization": f"Bearer {token}"}
    assert client_lan.get("/instances", headers=headers).headers["content-type"].startswith("application/json")
    assert client_lan.get("/instances", headers={**headers, "Accept": "*/*"}).headers["content-type"].startswith("application/json")


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


def test_loopback_websocket_needs_a_local_host_header():
    scope = _ws_scope(LOOPBACK)
    sent, reached = _run_gate(scope, PairingStore())
    assert sent == [{"type": "websocket.close", "code": 1008}] and reached == []

    scope = {**_ws_scope(LOOPBACK), "headers": [(b"host", b"127.0.0.1:8080")]}
    sent, reached = _run_gate(scope, PairingStore())
    assert sent == [] and reached == [True]


# -- advertised address ------------------------------------------------------

def _select_client(server_base, scope_server="keep"):
    """LAN-peer client whose scope["server"] comes from `server_base`."""
    from server.engine_runtime import EngineSelection
    manager = MagicMock()
    manager.list_instances.return_value = []
    manager.active = None
    inst = MagicMock()
    inst.id, inst.serial, inst.name = "adb:a", "a", "A"
    manager.get.return_value = inst
    manager.active = inst
    manager.select.return_value = EngineSelection(
        whep_url="http://x/whep", whep_token="t", generation=1, width=1, height=1)
    pairing = PairingStore()
    with patch("server.app.get_best_ip", return_value="100.64.1.4"):
        app = create_app(manager, pairing)
    asgi = app
    if scope_server != "keep":
        async def asgi(scope, receive, send):
            scope = dict(scope)
            if scope_server is None:
                scope.pop("server", None)
            else:
                scope["server"] = scope_server
            await app(scope, receive, send)
    client = TestClient(asgi, client=LAN, base_url=server_base)
    token = _pair(client, pairing)
    client.headers["Authorization"] = f"Bearer {token}"
    return client, manager


@pytest.mark.parametrize("server_base,expected", [
    ("http://192.168.1.10:8080", "192.168.1.10"),
    ("http://100.101.102.103:8080", "100.101.102.103"),
    ("http://0.0.0.0:8080", "100.64.1.4"),
    ("http://testserver", "100.64.1.4"),
])
def test_select_advertises_the_address_the_lan_client_reached(server_base, expected):
    client, manager = _select_client(server_base)
    with patch("server.app.get_best_ip", return_value="100.64.1.4"):
        response = client.post("/instances/adb:a/select")
    assert response.status_code == 200
    assert response.json()["ice_servers"] == [{"urls": f"stun:{expected}:3478"}]
    manager.select.assert_called_once_with("adb:a", expected)


@pytest.mark.parametrize("scope_server", [None, ("::", 8080)])
def test_select_falls_back_to_best_ip_without_a_usable_server_address(scope_server):
    client, manager = _select_client("http://192.168.1.10:8080", scope_server)
    with patch("server.app.get_best_ip", return_value="100.64.1.4"):
        response = client.post("/instances/adb:a/select")
    assert response.json()["ice_servers"] == [{"urls": "stun:100.64.1.4:3478"}]
    manager.select.assert_called_once_with("adb:a", "100.64.1.4")


def test_legacy_select_advertises_the_address_the_lan_client_reached():
    client, manager = _select_client("http://192.168.1.10:8080")
    with patch("server.app.get_best_ip", return_value="100.64.1.4"):
        response = client.post("/select", json={"id": "adb:a"})
    assert response.status_code == 200
    assert response.json()["stun_url"] == "stun:192.168.1.10:3478"
    manager.select.assert_called_once_with("a", "192.168.1.10")
