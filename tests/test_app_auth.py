import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import importlib
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _make_authed_client(token="s3cret", instances=None):
    os.environ["AUTH_TOKEN"] = token
    import config
    importlib.reload(config)
    from server import auth
    importlib.reload(auth)
    from server.stream import CaptureState, FrameQueue
    from server import app as app_module
    importlib.reload(app_module)

    state = CaptureState()
    fq = FrameQueue()
    im = MagicMock()
    im.list_instances.return_value = instances or []
    im.active = None
    with patch("server.app.get_best_ip", return_value="127.0.0.1"):
        app = app_module.create_app(state, fq, im)
    return TestClient(app), im


@pytest.fixture(autouse=True)
def _clear_auth_token():
    yield
    os.environ.pop("AUTH_TOKEN", None)
    import config
    importlib.reload(config)
    from server import auth
    importlib.reload(auth)


def test_protected_route_rejected_without_cookie():
    client, _ = _make_authed_client()
    r = client.get("/instances")
    assert r.status_code == 401


def test_login_wrong_token_rejected():
    client, _ = _make_authed_client()
    r = client.post("/login", json={"token": "wrong"})
    assert r.status_code == 401


def test_login_correct_token_sets_cookie_and_unlocks():
    client, _ = _make_authed_client()
    r = client.post("/login", json={"token": "s3cret"})
    assert r.status_code == 200
    assert "wc_session" in r.cookies
    r2 = client.get("/instances")
    assert r2.status_code == 200


def test_index_served_without_auth_so_login_page_can_load():
    client, _ = _make_authed_client()
    r = client.get("/")
    assert r.status_code != 401


def test_websocket_input_rejected_without_cookie():
    client, _ = _make_authed_client()
    with pytest.raises(Exception):
        with client.websocket_connect("/input"):
            pass


def test_public_ui_url_without_auth_token_refuses_to_start():
    os.environ["PUBLIC_UI_URL"] = "wss://tunnel.example.com/__tunnel/register"
    os.environ.pop("AUTH_TOKEN", None)
    import config
    importlib.reload(config)
    from server import auth
    importlib.reload(auth)
    from server.stream import CaptureState, FrameQueue
    from server import app as app_module
    importlib.reload(app_module)

    state = CaptureState()
    fq = FrameQueue()
    im = MagicMock()
    im.list_instances.return_value = []
    try:
        with patch("server.app.get_best_ip", return_value="127.0.0.1"):
            with pytest.raises(RuntimeError, match="AUTH_TOKEN"):
                app_module.create_app(state, fq, im)
    finally:
        os.environ.pop("PUBLIC_UI_URL", None)
        importlib.reload(config)
