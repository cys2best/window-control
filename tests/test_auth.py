import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import importlib
import time

import pytest

import config
from server import auth


@pytest.fixture(autouse=True)
def _clear_auth_token():
    yield
    _reload(None)


def _reload(token):
    if token is None:
        os.environ.pop("AUTH_TOKEN", None)
    else:
        os.environ["AUTH_TOKEN"] = token
    importlib.reload(config)
    importlib.reload(auth)


def test_auth_disabled_when_no_token():
    _reload(None)
    assert not auth.auth_enabled()


def test_auth_enabled_when_token_set():
    _reload("s3cret")
    assert auth.auth_enabled()


def test_check_token_accepts_correct_token():
    _reload("s3cret")
    assert auth.check_token("s3cret") is True


def test_check_token_rejects_wrong_token():
    _reload("s3cret")
    assert auth.check_token("nope") is False


def test_bearer_token_accepts_only_one_exact_bearer_credential():
    assert auth.bearer_token("Bearer s3cret") == "s3cret"


@pytest.mark.parametrize("value", [
    None, "", "s3cret", "Basic s3cret", "Bearer", "Bearer  s3cret",
    "bearer s3cret", "Bearer s3cret ", "Bearer s3 cret",
])
def test_bearer_token_rejects_malformed_authorization(value):
    assert auth.bearer_token(value) is None


def test_make_and_verify_session_cookie_roundtrip():
    _reload("s3cret")
    cookie = auth.make_session_cookie()
    assert auth.verify_session_cookie(cookie) is True


def test_verify_session_cookie_rejects_tampering():
    _reload("s3cret")
    cookie = auth.make_session_cookie()
    tampered = cookie[:-1] + ("a" if cookie[-1] != "a" else "b")
    assert auth.verify_session_cookie(tampered) is False


def test_verify_session_cookie_rejects_cookie_from_different_token():
    _reload("s3cret")
    cookie = auth.make_session_cookie()
    _reload("different-secret")
    assert auth.verify_session_cookie(cookie) is False


def test_verify_session_cookie_rejects_garbage():
    _reload("s3cret")
    assert auth.verify_session_cookie("not-a-real-cookie") is False
    assert auth.verify_session_cookie("") is False
    assert auth.verify_session_cookie(None) is False


def test_verify_session_cookie_rejects_expired():
    _reload("s3cret")
    old_cookie = auth.make_session_cookie(
        issued_at=time.time() - auth.SESSION_MAX_AGE_SECONDS - 1)
    assert auth.verify_session_cookie(old_cookie) is False
