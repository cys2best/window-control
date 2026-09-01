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
    from server import app as app_module
    importlib.reload(app_module)

    im = MagicMock()
    im.list_instances.return_value = instances or []
    im.active = None
    with patch("server.app.get_best_ip", return_value="127.0.0.1"):
        app = app_module.create_app(im)
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
    assert r.headers["www-authenticate"] == "Bearer"


def test_bearer_token_unlocks_native_control_api():
    client, _ = _make_authed_client()

    response = client.get(
        "/instances", headers={"Authorization": "Bearer s3cret"}
    )

    assert response.status_code == 200


@pytest.mark.parametrize("value", [
    "s3cret", "Basic s3cret", "Bearer", "Bearer  s3cret",
    "bearer s3cret", "Bearer wrong",
])
def test_malformed_or_wrong_bearer_is_rejected(value):
    client, _ = _make_authed_client()

    response = client.get("/instances", headers={"Authorization": value})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


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


def test_input_route_is_not_registered():
    client, _ = _make_authed_client()
    response = client.get("/input", headers={"Authorization": "Bearer s3cret"})
    assert response.status_code == 404


def test_public_ui_url_without_auth_token_refuses_to_start():
    os.environ["PUBLIC_UI_URL"] = "wss://tunnel.example.com/__tunnel/register"
    os.environ.pop("AUTH_TOKEN", None)
    import config
    importlib.reload(config)
    from server import auth
    importlib.reload(auth)
    from server import app as app_module
    importlib.reload(app_module)

    im = MagicMock()
    im.list_instances.return_value = []
    try:
        with patch("server.app.get_best_ip", return_value="127.0.0.1"):
            with pytest.raises(RuntimeError, match="AUTH_TOKEN"):
                app_module.create_app(im)
    finally:
        os.environ.pop("PUBLIC_UI_URL", None)
        importlib.reload(config)
