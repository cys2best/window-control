"""Supabase JWT auth gate.

Set SUPABASE_URL to require authentication. Verification checks the
ES256 signature against Supabase's public JWKS (fetched from
"<SUPABASE_URL>/auth/v1/.well-known/jwks.json" and cached by PyJWKClient,
refetched only when a token references an unrecognized key id — not a
per-request network round trip) plus `sub`/`exp` presence. Unset
SUPABASE_URL and every check here is a no-op — LAN-only deployments are
unaffected.
"""

import logging
from dataclasses import dataclass

import jwt
from jwt import PyJWKClient

import config

log = logging.getLogger(__name__)


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


_jwks_clients: dict[str, PyJWKClient] = {}


def _jwks_client() -> PyJWKClient | None:
    url = config.SUPABASE_URL
    if not url:
        return None
    client = _jwks_clients.get(url)
    if client is None:
        client = PyJWKClient(f"{url}/auth/v1/.well-known/jwks.json", cache_keys=True)
        _jwks_clients[url] = client
    return client


def verify_supabase_jwt(token: str | None) -> UserClaims | None:
    if not auth_enabled() or not token:
        return None
    client = _jwks_client()
    if client is None:
        return None

    try:
        signing_key = client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["ES256"],
            audience="authenticated",  # Supabase's fixed aud claim for session tokens
            options={"require": ["exp", "sub"]},
        )
    except Exception as error:
        # Fail closed on anything: unreachable JWKS endpoint, unknown kid,
        # bad/expired signature, malformed token, missing claims. Logged
        # (not silent) so a misconfiguration is diagnosable from the app's
        # own log instead of a manual repro session.
        log.info("Supabase JWT verification failed: %s: %s", type(error).__name__, error)
        return None

    sub = payload.get("sub")
    if not sub or not isinstance(sub, str):
        return None

    return UserClaims(user_id=sub, email=payload.get("email"))
