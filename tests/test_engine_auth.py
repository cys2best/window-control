import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import base64
import hashlib
import hmac
import json

import pytest
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from server.engine_auth import EngineTokenIssuer


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def decode_and_verify_eddsa(token: str, public_key) -> dict:
    header_b64, payload_b64, signature_b64 = token.split(".")
    signing_input = f"{header_b64}.{payload_b64}".encode()
    public_key.verify(_b64url_decode(signature_b64), signing_input)  # raises on mismatch
    return json.loads(_b64url_decode(payload_b64))


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


def test_engine_token_payload_contains_session_role_and_expiry():
    private_key = Ed25519PrivateKey.generate()
    issuer = EngineTokenIssuer("whep", private_key, clock=lambda: 1000.0)
    payload = decode_and_verify_eddsa(issuer.engine_token("user-1.instance0"), private_key.public_key())
    assert payload == {
        "session": "user-1.instance0", "role": "engine", "exp": 605800, "jti": "1",
    }


def test_engine_token_issuances_are_unique_and_verifiable():
    private_key = Ed25519PrivateKey.generate()
    issuer = EngineTokenIssuer("whep", private_key, clock=lambda: 1000.0)

    first = issuer.engine_token("user-1.instance0")
    second = issuer.engine_token("user-1.instance0")

    assert first != second
    first_claims = decode_and_verify_eddsa(first, private_key.public_key())
    second_claims = decode_and_verify_eddsa(second, private_key.public_key())
    assert first_claims["jti"] != second_claims["jti"]


def test_engine_token_rejects_tampering():
    private_key = Ed25519PrivateKey.generate()
    issuer = EngineTokenIssuer("whep", private_key, clock=lambda: 1000.0)
    token = issuer.engine_token("user-1.instance0")
    header_b64, payload_b64, signature_b64 = token.split(".")
    tampered_payload = base64.urlsafe_b64encode(b'{"session":"attacker.instance0","role":"engine","exp":605800,"jti":"1"}').rstrip(b"=").decode()
    tampered = f"{header_b64}.{tampered_payload}.{signature_b64}"
    with pytest.raises(InvalidSignature):
        decode_and_verify_eddsa(tampered, private_key.public_key())


def test_no_signaling_key_returns_empty_token_for_trusted_dev():
    issuer = EngineTokenIssuer("whep", None, clock=lambda: 1000.0)
    assert issuer.engine_token("instance0") == ""


def test_empty_whep_secret_is_invalid():
    with pytest.raises(ValueError):
        EngineTokenIssuer("", clock=lambda: 1000.0)
