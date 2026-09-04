import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import importlib
import time
from unittest.mock import MagicMock, patch

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from jwt import PyJWKClient

_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())


def _jwt(sub="user-1", email="a@example.com", exp_delta=3600):
    payload = {
        "sub": sub, "email": email, "aud": "authenticated",
        "exp": int(time.time()) + exp_delta,
    }
    return pyjwt.encode(payload, _PRIVATE_KEY, algorithm="ES256", headers={"kid": "test-kid"})


def _make_authed_client(instances=None):
    os.environ["SUPABASE_URL"] = "https://project.supabase.co"
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


class _FakeSigningKey:
    def __init__(self, key):
        self.key = key


@pytest.fixture(autouse=True)
def _clear_supabase_env(monkeypatch):
    # Verification logic (auth.verify_supabase_jwt) runs for real in every
    # test here -- only the network JWKS fetch is stubbed, to our own
    # known test key pair.
    monkeypatch.setattr(
        PyJWKClient, "get_signing_key_from_jwt",
        lambda self, token: _FakeSigningKey(_PRIVATE_KEY.public_key()),
    )
    yield
    for key in ("SUPABASE_URL", "PUBLIC_UI_URL", "TUNNEL_SECRET"):
        os.environ.pop(key, None)
    import config
    importlib.reload(config)
    from server import auth
    importlib.reload(auth)


def test_protected_route_rejected_without_token():
    client, _ = _make_authed_client()
    r = client.get("/instances")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_any_authenticated_user_sees_every_discovered_instance():
    client, im = _make_authed_client(
        instances=[{"id": "adb:a", "serial": "a"}, {"id": "adb:b", "serial": "b"}]
    )

    r = client.get("/instances", headers={"Authorization": f"Bearer {_jwt()}"})

    assert r.status_code == 200
    assert [i["id"] for i in r.json()] == ["adb:a", "adb:b"]


@pytest.mark.parametrize("value", [
    "garbage", "Basic s3cret", "Bearer", "Bearer  s3cret",
    "bearer s3cret",
])
def test_malformed_or_wrong_bearer_is_rejected(value):
    client, _ = _make_authed_client()
    response = client.get("/instances", headers={"Authorization": value})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_expired_jwt_is_rejected():
    client, _ = _make_authed_client()
    token = _jwt(exp_delta=-10)
    response = client.get("/instances", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_login_route_is_removed():
    client, _ = _make_authed_client()
    r = client.post("/login", json={"token": "anything"})
    assert r.status_code == 404


def test_index_served_without_auth_so_login_page_can_load():
    client, _ = _make_authed_client()
    r = client.get("/")
    assert r.status_code != 401


def test_auth_config_served_without_auth():
    client, _ = _make_authed_client()
    r = client.get("/auth/config")
    assert r.status_code == 200
    body = r.json()
    assert body["auth_enabled"] is True
    assert "supabase_url" in body
    assert "supabase_anon_key" in body


def test_scoped_routes_no_longer_check_ownership():
    client, im = _make_authed_client()
    im.get.return_value = MagicMock(id="adb:a", serial="a", name="i0")
    im.select.return_value = None  # short-circuit to 503 after passing authz

    r = client.post("/instances/adb:a/select", headers={"Authorization": f"Bearer {_jwt()}"})

    # 503 (engine not ready), not 401/403 -- proves the route only requires
    # a valid JWT now, no per-instance ownership check.
    assert r.status_code == 503


def test_legacy_select_no_longer_checks_ownership():
    client, im = _make_authed_client()
    im.select.return_value = MagicMock()  # Return a selection object
    im.active = MagicMock(id="adb:a", serial="a", name="i0")

    r = client.post(
        "/select", json={"id": "adb:a"}, headers={"Authorization": f"Bearer {_jwt()}"}
    )

    # Should succeed (200), not fail with 403/401 -- proves the route only
    # requires a valid JWT now, no per-instance ownership check.
    assert r.status_code == 200


def test_scoped_route_unaffected_by_ownership_when_auth_disabled():
    from unittest.mock import MagicMock as _MM
    im = _MM()
    im.list_instances.return_value = []
    im.get.return_value = None
    from server.app import create_app
    with patch("server.app.get_best_ip", return_value="127.0.0.1"):
        app = create_app(im)
    client = TestClient(app)

    # No auth configured: an unknown/unlinked instance still gets the
    # normal 404 (from the route's own existence check), never a 403 --
    # proves the LAN-only escape hatch in _authorize_instance_access.
    r = client.post("/instances/adb:a/select")
    assert r.status_code == 404


def test_public_ui_url_without_supabase_url_refuses_to_start():
    os.environ["PUBLIC_UI_URL"] = "wss://tunnel.example.com/__tunnel/register"
    os.environ.pop("SUPABASE_URL", None)
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
            with pytest.raises(RuntimeError, match="SUPABASE_URL"):
                app_module.create_app(im)
    finally:
        os.environ.pop("PUBLIC_UI_URL", None)
        importlib.reload(config)
