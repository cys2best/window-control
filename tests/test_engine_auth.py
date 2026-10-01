import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import hashlib
import hmac

import pytest

from server.engine_auth import EngineTokenIssuer


def test_whep_token_matches_cpp_fixture():
    issuer = EngineTokenIssuer("secret", clock=lambda: 1_700_000_000)
    token = issuer.whep("instance0")
    payload = "1700000300.instance0"
    expected = hmac.new(b"secret", payload.encode(), hashlib.sha256).hexdigest()
    assert token == f"{payload}.{expected}"


def test_whep_tokens_are_minted_from_the_current_clock_each_time():
    now = iter([1000.0, 1010.0])
    issuer = EngineTokenIssuer("whep", clock=lambda: next(now))
    first = issuer.whep("instance0")
    second = issuer.whep("instance0")
    assert first.split(".", 1)[0] == "1300"
    assert second.split(".", 1)[0] == "1310"


def test_empty_whep_secret_is_invalid():
    with pytest.raises(ValueError):
        EngineTokenIssuer("", clock=lambda: 1000.0)
