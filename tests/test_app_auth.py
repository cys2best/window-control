import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import importlib
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

SECRET = "test-jwt-secret"


def _jwt(sub="user-1", email="a@example.com", exp_delta=3600, secret=SECRET):
    import base64, hashlib, hmac, json

    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    header_b64 = b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = {"sub": sub, "email": email, "exp": int(time.time()) + exp_delta}
    payload_b64 = b64url(json.dumps(payload).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()
    sig = b64url(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return f"{header_b64}.{payload_b64}.{sig}"


def _make_authed_client(instances=None, supabase=None):
    os.environ["SUPABASE_URL"] = "https://project.supabase.co"
    os.environ["SUPABASE_JWT_SECRET"] = SECRET
    import config
    importlib.reload(config)
    from server import auth
    importlib.reload(auth)
    from server import app as app_module
    importlib.reload(app_module)

    im = MagicMock()
    im.list_instances.return_value = instances or []
    im.active = None
    supabase = supabase or MagicMock()
    with patch("server.app.get_best_ip", return_value="127.0.0.1"), \
         patch("server.app.SupabaseClient", return_value=supabase):
        app = app_module.create_app(im)
    return TestClient(app), im, supabase


@pytest.fixture(autouse=True)
def _clear_supabase_env():
    yield
    for key in ("SUPABASE_URL", "SUPABASE_JWT_SECRET", "PUBLIC_UI_URL", "TUNNEL_SECRET"):
        os.environ.pop(key, None)
    import config
    importlib.reload(config)
    from server import auth
    importlib.reload(auth)


def test_protected_route_rejected_without_token():
    client, _, _ = _make_authed_client()
    r = client.get("/instances")
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == "Bearer"


def test_valid_jwt_unlocks_instances_and_filters_by_device_links():
    client, im, supabase = _make_authed_client(
        instances=[{"id": "adb:a", "serial": "a"}, {"id": "adb:b", "serial": "b"}]
    )
    supabase.list_linked_instance_ids.return_value = ["adb:a"]

    r = client.get("/instances", headers={"Authorization": f"Bearer {_jwt()}"})

    assert r.status_code == 200
    assert [i["id"] for i in r.json()] == ["adb:a"]
    supabase.list_linked_instance_ids.assert_called_once_with("user-1")


@pytest.mark.parametrize("value", [
    "garbage", "Basic s3cret", "Bearer", "Bearer  s3cret",
    "bearer s3cret",
])
def test_malformed_or_wrong_bearer_is_rejected(value):
    client, _, _ = _make_authed_client()
    response = client.get("/instances", headers={"Authorization": value})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_expired_jwt_is_rejected():
    client, _, _ = _make_authed_client()
    token = _jwt(exp_delta=-10)
    response = client.get("/instances", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_login_route_is_removed():
    client, _, _ = _make_authed_client()
    r = client.post("/login", json={"token": "anything"})
    assert r.status_code == 404


def test_index_served_without_auth_so_login_page_can_load():
    client, _, _ = _make_authed_client()
    r = client.get("/")
    assert r.status_code != 401


def test_auth_config_served_without_auth():
    client, _, _ = _make_authed_client()
    r = client.get("/auth/config")
    assert r.status_code == 200
    body = r.json()
    assert body["auth_enabled"] is True
    assert "supabase_url" in body
    assert "supabase_anon_key" in body


def test_link_instance_succeeds_when_unclaimed():
    client, im, supabase = _make_authed_client(instances=[{"id": "adb:a", "serial": "a"}])
    im.get.return_value = MagicMock(id="adb:a")
    supabase.link_instance.return_value = True

    r = client.post("/instances/adb:a/link", headers={"Authorization": f"Bearer {_jwt()}"})

    assert r.status_code == 200
    supabase.link_instance.assert_called_once_with("user-1", "adb:a")


def test_link_instance_conflict_when_already_claimed():
    client, im, supabase = _make_authed_client(instances=[{"id": "adb:a", "serial": "a"}])
    im.get.return_value = MagicMock(id="adb:a")
    supabase.link_instance.return_value = False

    r = client.post("/instances/adb:a/link", headers={"Authorization": f"Bearer {_jwt()}"})

    assert r.status_code == 409


def test_link_unknown_instance_404s():
    client, im, supabase = _make_authed_client()
    im.get.return_value = None

    r = client.post("/instances/adb:missing/link", headers={"Authorization": f"Bearer {_jwt()}"})

    assert r.status_code == 404


def test_unlink_instance():
    client, im, supabase = _make_authed_client()

    r = client.delete("/instances/adb:a/link", headers={"Authorization": f"Bearer {_jwt()}"})

    assert r.status_code == 200
    supabase.unlink_instance.assert_called_once_with("user-1", "adb:a")


def test_supabase_unavailable_on_instances_fails_closed_401():
    from server.supabase_client import SupabaseUnavailable
    client, im, supabase = _make_authed_client(instances=[{"id": "adb:a", "serial": "a"}])
    supabase.list_linked_instance_ids.side_effect = SupabaseUnavailable("boom")

    r = client.get("/instances", headers={"Authorization": f"Bearer {_jwt()}"})

    assert r.status_code == 401


def test_select_instance_403s_when_not_linked():
    client, im, supabase = _make_authed_client()
    im.get.return_value = MagicMock(id="adb:a", serial="a", name="i0")
    supabase.list_linked_instance_ids.return_value = ["adb:other"]

    r = client.post("/instances/adb:a/select", headers={"Authorization": f"Bearer {_jwt()}"})

    assert r.status_code == 403
    im.select.assert_not_called()


def test_set_instance_quality_403s_when_not_linked():
    client, im, supabase = _make_authed_client()
    im.get.return_value = MagicMock(id="adb:a")
    supabase.list_linked_instance_ids.return_value = ["adb:other"]

    r = client.post(
        "/instances/adb:a/quality",
        json={"tier": "720"},
        headers={"Authorization": f"Bearer {_jwt()}"},
    )

    assert r.status_code == 403
    im.set_tier.assert_not_called()


def test_request_keyframe_403s_when_not_linked():
    client, im, supabase = _make_authed_client()
    supabase.list_linked_instance_ids.return_value = ["adb:other"]

    r = client.post("/instances/adb:a/keyframe", headers={"Authorization": f"Bearer {_jwt()}"})

    assert r.status_code == 403
    im.request_keyframe.assert_not_called()


def test_instance_preview_403s_when_not_linked():
    client, im, supabase = _make_authed_client()
    supabase.list_linked_instance_ids.return_value = ["adb:other"]

    r = client.get("/instances/adb:a/preview", headers={"Authorization": f"Bearer {_jwt()}"})

    assert r.status_code == 403


def test_scoped_routes_allow_access_when_instance_is_linked():
    client, im, supabase = _make_authed_client()
    im.get.return_value = MagicMock(id="adb:a", serial="a", name="i0")
    im.select.return_value = None  # short-circuit to 503 after passing authz
    supabase.list_linked_instance_ids.return_value = ["adb:a"]

    r = client.post("/instances/adb:a/select", headers={"Authorization": f"Bearer {_jwt()}"})

    # 503 (engine not ready), not 403/401 -- proves authz passed and the
    # route reached its normal not-ready path.
    assert r.status_code == 503


def test_legacy_select_403s_when_not_linked_using_raw_id():
    client, im, supabase = _make_authed_client()
    supabase.list_linked_instance_ids.return_value = ["adb:other"]

    r = client.post(
        "/select", json={"id": "adb:a"}, headers={"Authorization": f"Bearer {_jwt()}"}
    )

    assert r.status_code == 403
    im.select.assert_not_called()
    # Authorized against the raw "adb:a" id, not the stripped "a" serial.
    supabase.list_linked_instance_ids.assert_called_once_with("user-1")


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
