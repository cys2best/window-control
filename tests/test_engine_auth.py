import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import base64
import hashlib
import hmac
import json

import pytest

from server.engine_auth import EngineTokenIssuer


def decode_and_verify_hs256(token: str, secret: str) -> dict:
    header, payload, signature = token.split(".")
    signing_input = f"{header}.{payload}".encode()
    expected = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    assert hmac.compare_digest(signature, expected)
    padded = payload + "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(padded))


def test_whep_token_matches_cpp_fixture():
    issuer = EngineTokenIssuer("secret", clock=lambda: 1_700_000_000)
    token = issuer.whep("instance0")
    payload = "1700000300.instance0"
    expected = hmac.new(b"secret", payload.encode(), hashlib.sha256).hexdigest()
    assert token == f"{payload}.{expected}"


def test_tokens_are_minted_from_the_current_clock_each_time():
    now = iter([1000.0, 1010.0])
    issuer = EngineTokenIssuer("whep", "signal", clock=lambda: next(now))
    first = issuer.whep("instance0")
    second = issuer.whep("instance0")
    assert first.split(".", 1)[0] == "1300"
    assert second.split(".", 1)[0] == "1310"


def test_fixed_clock_issuances_are_unique_and_keep_verifiable_claims():
    issuer = EngineTokenIssuer("whep", "signal", clock=lambda: 1000.0)

    first_whep = issuer.whep("instance0")
    second_whep = issuer.whep("instance0")
    first_viewer = issuer.signaling("instance0", "viewer")
    second_viewer = issuer.signaling("instance0", "viewer")

    assert first_whep != second_whep
    for token in (first_whep, second_whep):
        expiry, instance_name, signature = token.split(".")
        assert instance_name == "instance0"
        expected = hmac.new(
            b"whep", f"{expiry}.{instance_name}".encode(), hashlib.sha256
        ).hexdigest()
        assert hmac.compare_digest(signature, expected)
        assert int(expiry) >= 1300

    first_claims = decode_and_verify_hs256(first_viewer, "signal")
    second_claims = decode_and_verify_hs256(second_viewer, "signal")
    assert first_claims["session"] == second_claims["session"] == "instance0"
    assert first_claims["role"] == second_claims["role"] == "viewer"
    assert first_claims["exp"] == second_claims["exp"] == 1300
    assert first_claims["jti"] != second_claims["jti"]


def test_signaling_payload_contains_session_role_and_expiry():
    issuer = EngineTokenIssuer("whep", "signal", clock=lambda: 1000.0)
    payload = decode_and_verify_hs256(issuer.signaling("instance0", "engine"), "signal")
    assert payload == {
        "session": "instance0", "role": "engine", "exp": 605800, "jti": "1",
    }


def test_signaling_viewer_role_uses_short_ttl_distinct_from_engine():
    issuer = EngineTokenIssuer("whep", "signal", clock=lambda: 1000.0)
    viewer_payload = decode_and_verify_hs256(issuer.signaling("instance0", "viewer"), "signal")
    engine_payload = decode_and_verify_hs256(issuer.signaling("instance0", "engine"), "signal")
    assert viewer_payload["exp"] == 1300
    assert engine_payload["exp"] == 605800
    assert viewer_payload["exp"] != engine_payload["exp"]


def test_signaling_rejects_invalid_role():
    issuer = EngineTokenIssuer("whep", "signal", clock=lambda: 1000.0)
    with pytest.raises(ValueError):
        issuer.signaling("instance0", "admin")


def test_empty_signaling_secret_returns_empty_token_for_trusted_dev():
    issuer = EngineTokenIssuer("whep", "", clock=lambda: 1000.0)
    assert issuer.signaling("instance0", "engine") == ""
    assert issuer.signaling("instance0", "viewer") == ""


def test_empty_whep_secret_is_invalid():
    with pytest.raises(ValueError):
        EngineTokenIssuer("", clock=lambda: 1000.0)
