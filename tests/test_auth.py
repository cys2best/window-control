import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import importlib
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from jwt import PyJWKClient

import config
from server import auth

_KEY_ID = "test-kid-1"
_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())
_OTHER_PRIVATE_KEY = ec.generate_private_key(ec.SECP256R1())


class _FakeSigningKey:
    def __init__(self, key):
        self.key = key


@pytest.fixture(autouse=True)
def _clear_supabase_env(monkeypatch):
    yield
    for key in ("SUPABASE_URL",):
        os.environ.pop(key, None)
    importlib.reload(config)
    importlib.reload(auth)


def _reload(url):
    if url is None:
        os.environ.pop("SUPABASE_URL", None)
    else:
        os.environ["SUPABASE_URL"] = url
    importlib.reload(config)
    importlib.reload(auth)


def _mock_jwks(monkeypatch, *, public_key=None):
    """Stub the network JWKS fetch; verification logic itself stays real."""
    key = public_key if public_key is not None else _PRIVATE_KEY.public_key()
    monkeypatch.setattr(
        PyJWKClient, "get_signing_key_from_jwt", lambda self, token: _FakeSigningKey(key)
    )


def _make_jwt(payload: dict, *, private_key=None, headers=None) -> str:
    key = private_key if private_key is not None else _PRIVATE_KEY
    payload = {"aud": "authenticated", **payload}
    return jwt.encode(payload, key, algorithm="ES256", headers=headers or {"kid": _KEY_ID})


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


def test_verify_supabase_jwt_accepts_valid_token(monkeypatch):
    _reload("https://project.supabase.co")
    _mock_jwks(monkeypatch)
    token = _make_jwt({
        "sub": "user-123", "email": "a@example.com",
        "exp": int(time.time()) + 3600,
    })
    claims = auth.verify_supabase_jwt(token)
    assert claims == auth.UserClaims(user_id="user-123", email="a@example.com")


def test_verify_supabase_jwt_rejects_when_auth_disabled(monkeypatch):
    _reload(None)
    _mock_jwks(monkeypatch)
    token = _make_jwt({"sub": "user-123", "exp": int(time.time()) + 3600})
    assert auth.verify_supabase_jwt(token) is None


def test_verify_supabase_jwt_rejects_none_and_empty(monkeypatch):
    _reload("https://project.supabase.co")
    _mock_jwks(monkeypatch)
    assert auth.verify_supabase_jwt(None) is None
    assert auth.verify_supabase_jwt("") is None


def test_verify_supabase_jwt_rejects_bad_signature(monkeypatch):
    _reload("https://project.supabase.co")
    # JWKS returns the real public key, but the token was signed by a
    # different private key — signature must not verify.
    _mock_jwks(monkeypatch)
    token = _make_jwt(
        {"sub": "user-123", "exp": int(time.time()) + 3600},
        private_key=_OTHER_PRIVATE_KEY,
    )
    assert auth.verify_supabase_jwt(token) is None


def test_verify_supabase_jwt_rejects_expired(monkeypatch):
    _reload("https://project.supabase.co")
    _mock_jwks(monkeypatch)
    token = _make_jwt({"sub": "user-123", "exp": int(time.time()) - 1})
    assert auth.verify_supabase_jwt(token) is None


def test_verify_supabase_jwt_rejects_missing_exp_or_sub(monkeypatch):
    _reload("https://project.supabase.co")
    _mock_jwks(monkeypatch)
    assert auth.verify_supabase_jwt(_make_jwt({"sub": "user-123"})) is None
    assert auth.verify_supabase_jwt(
        _make_jwt({"exp": int(time.time()) + 3600})
    ) is None


def test_verify_supabase_jwt_rejects_malformed_token(monkeypatch):
    _reload("https://project.supabase.co")
    _mock_jwks(monkeypatch)
    assert auth.verify_supabase_jwt("not-a-jwt") is None
    assert auth.verify_supabase_jwt("a.b") is None


def test_verify_supabase_jwt_claims_email_optional(monkeypatch):
    _reload("https://project.supabase.co")
    _mock_jwks(monkeypatch)
    token = _make_jwt({"sub": "user-123", "exp": int(time.time()) + 3600})
    claims = auth.verify_supabase_jwt(token)
    assert claims.user_id == "user-123"
    assert claims.email is None


def test_verify_supabase_jwt_rejects_wrong_or_missing_audience(monkeypatch):
    _reload("https://project.supabase.co")
    _mock_jwks(monkeypatch)
    wrong_aud = _make_jwt({
        "sub": "user-123", "exp": int(time.time()) + 3600, "aud": "something-else",
    })
    assert auth.verify_supabase_jwt(wrong_aud) is None
    no_aud = jwt.encode(
        {"sub": "user-123", "exp": int(time.time()) + 3600},
        _PRIVATE_KEY, algorithm="ES256", headers={"kid": _KEY_ID},
    )
    assert auth.verify_supabase_jwt(no_aud) is None


def test_verify_supabase_jwt_rejects_when_jwks_lookup_fails(monkeypatch):
    _reload("https://project.supabase.co")

    def _raise(self, token):
        raise jwt.exceptions.PyJWKClientError("unreachable")

    monkeypatch.setattr(PyJWKClient, "get_signing_key_from_jwt", _raise)
    token = _make_jwt({"sub": "user-123", "exp": int(time.time()) + 3600})
    assert auth.verify_supabase_jwt(token) is None
