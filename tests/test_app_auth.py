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


def _make_authed_client(instances=None, supabase=None):
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
    supabase = supabase or MagicMock()
    with patch("server.app.get_best_ip", return_value="127.0.0.1"), \
         patch("server.app.SupabaseClient", return_value=supabase):
        app = app_module.create_app(im)
    return TestClient(app), im, supabase


class _FakeSigningKey:
    def __init__(self, key):
        self.key = key


@pytest.fixture(autouse=True)
def _clear_supabase_env(monkeypatch, tmp_path):
    # Verification logic (auth.verify_supabase_jwt) runs for real in every
    # test here -- only the network JWKS fetch is stubbed, to our own
    # known test key pair.
    monkeypatch.setattr(
        PyJWKClient, "get_signing_key_from_jwt",
        lambda self, token: _FakeSigningKey(_PRIVATE_KEY.public_key()),
    )
    # create_app() reads/writes install_identity's real keypair + owner
    # cache files on every call (auth-enabled here in every test). Point
    # those at a throwaway tmp dir instead of install_identity's real
    # candidate paths, or the owner cache persists across test runs and
    # pollutes later tests -- same isolation test_install_identity.py uses.
    from server import install_identity
    monkeypatch.setattr(install_identity, "_CANDIDATE_DIRS", [str(tmp_path)])
    yield
    for key in ("SUPABASE_URL", "PUBLIC_UI_URL", "TUNNEL_SECRET"):
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


def test_any_authenticated_user_sees_every_discovered_instance():
    client, im, _ = _make_authed_client(
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
    client, _, _ = _make_authed_client()
    response = client.get("/instances", headers={"Authorization": value})
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_expired_jwt_is_rejected():
    client, _, _ = _make_authed_client()
    token = _jwt(exp_delta=-10)
    response = client.get("/instances", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401


def test_legacy_login_post_is_removed():
    # "/login" now exists again as apps/web's GET-only login page-shell
    # route (see test_web_page_shells_served_without_auth above), so a POST
    # to it correctly 405s (path exists, method doesn't) rather than 404ing
    # -- the thing this test actually guards, that the legacy shared-secret
    # POST /login auth mechanism itself is gone, still holds either way.
    client, _, _ = _make_authed_client()
    r = client.post("/login", json={"token": "anything"})
    assert r.status_code == 405


def test_index_served_without_auth_so_login_page_can_load():
    client, _, _ = _make_authed_client()
    r = client.get("/")
    assert r.status_code != 401


@pytest.mark.parametrize("path", ["/login", "/stream"])
def test_web_page_shells_served_without_auth(path):
    # apps/web's page-shell HTML routes carry no user data (same reasoning
    # that already exempted "/") -- an unauthenticated visitor must be able
    # to reach /login at all, and /stream's own shell is no more
    # sensitive than /'s was under the old single-page client. Real
    # protection is enforced at the JSON API layer, which stays gated.
    client, _, _ = _make_authed_client()
    r = client.get(path)
    assert r.status_code != 401


def test_retired_setup_route_returns_404():
    client, _, _ = _make_authed_client()
    r = client.get("/setup")
    assert r.status_code == 404


@pytest.mark.parametrize(
    "path", ["/index.txt", "/login.txt", "/stream.txt", "/instances.txt"]
)
def test_rsc_payloads_served_without_auth(path):
    # Next's client router fetches these on every soft navigation with no
    # Authorization header of its own; a 401 makes it hard-navigate
    # instead. They are build-time-prerendered shells with no user data --
    # the same content the exempt .html shells already carry.
    client, _, _ = _make_authed_client()
    r = client.get(path)
    assert r.status_code != 401


def test_browser_navigation_to_instances_gets_the_shell_not_a_401():
    # The post-login landing page must render for a browser even before
    # its JS has attached a bearer token to anything.
    client, _, _ = _make_authed_client()
    r = client.get("/instances", headers={"Accept": "text/html,*/*;q=0.8"})
    assert r.status_code != 401


def test_instances_json_still_requires_auth_for_api_shaped_requests():
    # The exemption above must not leak the instance list: same path, no
    # Accept: text/html, still gated.
    client, _, _ = _make_authed_client(instances=[{"id": "adb:a", "serial": "a"}])
    for headers in ({}, {"Accept": "*/*"}, {"Accept": "application/json"}):
        r = client.get("/instances", headers=headers)
        assert r.status_code == 401, headers


def test_browser_shaped_instances_request_never_returns_instance_data():
    # The auth gate and the route handler branch on the same predicate, so
    # an unauthenticated HTML-preferring request can only ever receive the
    # static shell -- never the list. Proven against a manager that would
    # happily hand over real instances if asked.
    import server.app as app_module
    client, im, _ = _make_authed_client(instances=[{"id": "adb:secret", "serial": "secret"}])
    r = client.get("/instances", headers={"Accept": "text/html,*/*;q=0.8"})
    assert r.status_code != 401
    assert "secret" not in r.text
    assert not r.headers["content-type"].startswith("application/json")


def test_manifest_served_without_auth():
    # A PWA fetches the manifest before any login exists.
    client, _, _ = _make_authed_client()
    r = client.get("/manifest.json")
    assert r.status_code != 401


def test_next_static_assets_served_without_auth():
    client, _, _ = _make_authed_client()
    r = client.get("/_next/static/chunks/does-not-exist.js")
    # 404 (no such file) is fine -- the point is the auth gate doesn't
    # intercept it with a 401 first.
    assert r.status_code != 401


def test_auth_config_served_without_auth():
    client, _, _ = _make_authed_client()
    r = client.get("/auth/config")
    assert r.status_code == 200
    body = r.json()
    assert body["auth_enabled"] is True
    assert "supabase_url" in body
    assert "supabase_anon_key" in body


def test_scoped_routes_no_longer_check_ownership():
    client, im, _ = _make_authed_client()
    im.get.return_value = MagicMock(id="adb:a", serial="a", name="i0")
    im.select.return_value = None  # short-circuit to 503 after passing authz

    r = client.post("/instances/adb:a/select", headers={"Authorization": f"Bearer {_jwt()}"})

    # 503 (engine not ready), not 401/403 -- proves the route only requires
    # a valid JWT now, no per-instance ownership check.
    assert r.status_code == 503


def test_legacy_select_no_longer_checks_ownership():
    client, im, _ = _make_authed_client()
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


def test_login_claims_install_once_then_locks_to_that_owner():
    client, _, supabase = _make_authed_client()

    first = client.get("/instances", headers={"Authorization": f"Bearer {_jwt(sub='user-1')}"})
    again = client.get("/instances", headers={"Authorization": f"Bearer {_jwt(sub='user-1')}"})
    different = client.get("/instances", headers={"Authorization": f"Bearer {_jwt(sub='user-2')}"})

    assert first.status_code == 200
    assert again.status_code == 200
    # A second, different account must NOT be able to seize this install's
    # ownership just by authenticating against it.
    assert different.status_code == 403
    assert supabase.upsert_install.call_count == 1
    assert supabase.upsert_install.call_args.args[0] == "user-1"


def test_login_upsert_failure_does_not_fail_the_request(monkeypatch):
    from server.supabase_client import SupabaseUnavailable
    client, _, supabase = _make_authed_client()
    supabase.upsert_install.side_effect = SupabaseUnavailable("boom")

    r = client.get("/instances", headers={"Authorization": f"Bearer {_jwt()}"})

    assert r.status_code == 200
