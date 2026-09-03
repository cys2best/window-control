import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import base64
import hashlib
import hmac
import importlib
import json
import time

import pytest

import config
from server import auth

SECRET = "test-jwt-secret"


@pytest.fixture(autouse=True)
def _clear_supabase_env():
    yield
    for key in ("SUPABASE_URL", "SUPABASE_JWT_SECRET"):
        os.environ.pop(key, None)
    importlib.reload(config)
    importlib.reload(auth)


def _reload(url, secret=SECRET):
    if url is None:
        os.environ.pop("SUPABASE_URL", None)
    else:
        os.environ["SUPABASE_URL"] = url
    if secret is None:
        os.environ.pop("SUPABASE_JWT_SECRET", None)
    else:
        os.environ["SUPABASE_JWT_SECRET"] = secret
    importlib.reload(config)
    importlib.reload(auth)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _make_jwt(payload: dict, secret: str = SECRET) -> str:
    header_b64 = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload_b64 = _b64url(json.dumps(payload).encode())
    signing_input = f"{header_b64}.{payload_b64}".encode()
    sig = _b64url(hmac.new(secret.encode(), signing_input, hashlib.sha256).digest())
    return f"{header_b64}.{payload_b64}.{sig}"


def test_auth_disabled_when_no_supabase_url():
    _reload(None)
    assert not auth.auth_enabled()


def test_auth_enabled_when_supabase_url_set():
    _reload("https://project.supabase.co")
    assert auth.auth_enabled()


def test_bearer_token_accepts_only_one_exact_bearer_credential():
    assert auth.bearer_token("Bearer s3cret") == "s3cret"


@pytest.mark.parametrize("value", [
    None, "", "s3cret", "Basic s3cret", "Bearer", "Bearer  s3cret",
    "bearer s3cret", "Bearer s3cret ", "Bearer s3 cret",
])
def test_bearer_token_rejects_malformed_authorization(value):
    assert auth.bearer_token(value) is None


def test_verify_supabase_jwt_accepts_valid_token():
    _reload("https://project.supabase.co")
    token = _make_jwt({
        "sub": "user-123", "email": "a@example.com",
        "exp": int(time.time()) + 3600,
    })
    claims = auth.verify_supabase_jwt(token)
    assert claims == auth.UserClaims(user_id="user-123", email="a@example.com")


def test_verify_supabase_jwt_rejects_when_auth_disabled():
    _reload(None)
    token = _make_jwt({"sub": "user-123", "exp": int(time.time()) + 3600})
    assert auth.verify_supabase_jwt(token) is None


def test_verify_supabase_jwt_rejects_none_and_empty():
    _reload("https://project.supabase.co")
    assert auth.verify_supabase_jwt(None) is None
    assert auth.verify_supabase_jwt("") is None


def test_verify_supabase_jwt_rejects_bad_signature():
    _reload("https://project.supabase.co")
    token = _make_jwt(
        {"sub": "user-123", "exp": int(time.time()) + 3600}, secret="wrong-secret"
    )
    assert auth.verify_supabase_jwt(token) is None


def test_verify_supabase_jwt_rejects_expired():
    _reload("https://project.supabase.co")
    token = _make_jwt({"sub": "user-123", "exp": int(time.time()) - 1})
    assert auth.verify_supabase_jwt(token) is None


def test_verify_supabase_jwt_rejects_missing_exp_or_sub():
    _reload("https://project.supabase.co")
    assert auth.verify_supabase_jwt(_make_jwt({"sub": "user-123"})) is None
    assert auth.verify_supabase_jwt(
        _make_jwt({"exp": int(time.time()) + 3600})
    ) is None


def test_verify_supabase_jwt_rejects_malformed_token():
    _reload("https://project.supabase.co")
    assert auth.verify_supabase_jwt("not-a-jwt") is None
    assert auth.verify_supabase_jwt("a.b") is None


def test_verify_supabase_jwt_claims_email_optional():
    _reload("https://project.supabase.co")
    token = _make_jwt({"sub": "user-123", "exp": int(time.time()) + 3600})
    claims = auth.verify_supabase_jwt(token)
    assert claims.user_id == "user-123"
    assert claims.email is None


def test_verify_supabase_jwt_rejects_when_secret_is_empty():
    _reload("https://project.supabase.co", secret="")
    token = _make_jwt(
        {"sub": "user-123", "exp": int(time.time()) + 3600}, secret=""
    )
    assert auth.verify_supabase_jwt(token) is None


def test_verify_supabase_jwt_rejects_non_dict_json_payload():
    _reload("https://project.supabase.co")
    # Manually construct a JWT with a non-dict payload (array)
    header_b64 = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload_b64 = _b64url(json.dumps([1, 2, 3]).encode())  # Array, not object
    signing_input = f"{header_b64}.{payload_b64}".encode()
    sig = _b64url(hmac.new(SECRET.encode(), signing_input, hashlib.sha256).digest())
    token = f"{header_b64}.{payload_b64}.{sig}"
    assert auth.verify_supabase_jwt(token) is None
