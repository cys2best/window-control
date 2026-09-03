"""Supabase JWT auth gate.

Set SUPABASE_URL (+ SUPABASE_JWT_SECRET) to require authentication.
Verification is entirely local: an HS256 signature check against
SUPABASE_JWT_SECRET plus an `exp` check, so it never depends on Supabase
being reachable. Unset SUPABASE_URL and every check here is a no-op —
LAN-only deployments are unaffected.
"""

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass

import config


@dataclass(frozen=True)
class UserClaims:
    user_id: str
    email: str | None


def auth_enabled() -> bool:
    return bool(config.SUPABASE_URL)


def bearer_token(authorization: str | None) -> str | None:
    """Return one exact Bearer credential, rejecting every other form."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    if not token or token.strip() != token or " " in token:
        return None
    return token


def _b64url_decode(segment: str) -> bytes:
    padding = "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(segment + padding)


def verify_supabase_jwt(token: str | None) -> UserClaims | None:
    if not auth_enabled() or not token:
        return None

    parts = token.split(".")
    if len(parts) != 3:
        return None
    header_b64, payload_b64, signature_b64 = parts

    signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
    expected_sig = base64.urlsafe_b64encode(
        hmac.new(
            config.SUPABASE_JWT_SECRET.encode(), signing_input, hashlib.sha256
        ).digest()
    ).rstrip(b"=").decode()
    if not hmac.compare_digest(signature_b64, expected_sig):
        return None

    try:
        payload = json.loads(_b64url_decode(payload_b64))
    except (ValueError, json.JSONDecodeError):
        return None

    sub = payload.get("sub")
    exp = payload.get("exp")
    if not sub or not isinstance(sub, str):
        return None
    if not isinstance(exp, (int, float)) or isinstance(exp, bool):
        return None
    if time.time() >= exp:
        return None

    return UserClaims(user_id=sub, email=payload.get("email"))
