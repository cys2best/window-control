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
import time
from dataclasses import dataclass

import httpx
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
_token_cache: dict[str, tuple[float, UserClaims]] = {}


def _jwks_client() -> PyJWKClient | None:
    url = config.SUPABASE_URL
    if not url:
        return None
    url = url.rstrip("/")
    client = _jwks_clients.get(url)
    if client is None:
        client = PyJWKClient(f"{url}/auth/v1/.well-known/jwks.json", cache_keys=True)
        _jwks_clients[url] = client
    return client


def _fetch_user_from_supabase(url: str, token: str) -> UserClaims | None:
    api_key = getattr(config, "SUPABASE_SERVICE_ROLE_KEY", "") or getattr(config, "SUPABASE_ANON_KEY", "")
    if not api_key:
        log.warning("Cannot verify token with Supabase REST API: neither SUPABASE_SERVICE_ROLE_KEY nor SUPABASE_ANON_KEY is set")
        return None
    try:
        r = httpx.get(
            f"{url}/auth/v1/user",
            headers={"Authorization": f"Bearer {token}", "apikey": api_key},
            timeout=5.0,
        )
        if r.status_code == 200:
            data = r.json()
            sub = data.get("id")
            if sub and isinstance(sub, str):
                return UserClaims(user_id=sub, email=data.get("email"))
        else:
            log.info("Supabase /auth/v1/user returned %s: %s", r.status_code, r.text)
    except Exception as error:
        log.info("Supabase /auth/v1/user request failed: %s: %s", type(error).__name__, error)
    return None


def verify_supabase_jwt(token: str | None) -> UserClaims | None:
    if not auth_enabled() or not token:
        return None

    now = time.time()
    cached = _token_cache.get(token)
    if cached is not None:
        expiry, claims = cached
        if now < expiry:
            return claims
        _token_cache.pop(token, None)

    try:
        header = jwt.get_unverified_header(token)
    except Exception as error:
        log.info("Supabase JWT header decode failed: %s: %s", type(error).__name__, error)
        return None

    url = config.SUPABASE_URL.rstrip("/")
    alg = header.get("alg")
    claims: UserClaims | None = None

    try:
        unverified_payload = jwt.decode(token, options={"verify_signature": False, "verify_exp": False, "verify_aud": False})
    except Exception:
        unverified_payload = {}

    if alg == "HS256":
        jwt_secret = getattr(config, "SUPABASE_JWT_SECRET", None)
        if jwt_secret:
            try:
                payload = jwt.decode(
                    token,
                    jwt_secret,
                    algorithms=["HS256"],
                    audience="authenticated",
                    options={"require": ["exp", "sub"]},
                )
                sub = payload.get("sub")
                if sub and isinstance(sub, str):
                    claims = UserClaims(user_id=sub, email=payload.get("email"))
            except (jwt.InvalidSignatureError, jwt.ExpiredSignatureError, jwt.InvalidAudienceError, jwt.MissingRequiredClaimError) as error:
                log.info(
                    "Supabase JWT HS256 verification failed: %s: %s (claims: aud=%s exp=%s now=%s, diff=%ss)",
                    type(error).__name__, error, unverified_payload.get("aud"), unverified_payload.get("exp"), int(now),
                    int(unverified_payload.get("exp", 0)) - int(now) if unverified_payload.get("exp") else "N/A"
                )
            except Exception as error:
                log.info("Supabase JWT HS256 decode error: %s: %s", type(error).__name__, error)
        if claims is None:
            claims = _fetch_user_from_supabase(url, token)

    elif alg == "ES256":
        client = _jwks_client()
        if client is not None:
            try:
                signing_key = client.get_signing_key_from_jwt(token)
                payload = jwt.decode(
                    token,
                    signing_key.key,
                    algorithms=["ES256"],
                    audience="authenticated",
                    options={"require": ["exp", "sub"]},
                )
                sub = payload.get("sub")
                if sub and isinstance(sub, str):
                    claims = UserClaims(user_id=sub, email=payload.get("email"))
            except (jwt.InvalidSignatureError, jwt.ExpiredSignatureError, jwt.InvalidAudienceError, jwt.MissingRequiredClaimError) as error:
                log.info(
                    "Supabase JWT ES256 verification failed: %s: %s (claims: aud=%s exp=%s now=%s, diff=%ss)",
                    type(error).__name__, error, unverified_payload.get("aud"), unverified_payload.get("exp"), int(now),
                    int(unverified_payload.get("exp", 0)) - int(now) if unverified_payload.get("exp") else "N/A"
                )
            except Exception as error:
                log.info("Supabase JWKS verification attempt failed: %s: %s", type(error).__name__, error)
        if claims is None:
            claims = _fetch_user_from_supabase(url, token)

    else:
        claims = _fetch_user_from_supabase(url, token)

    if claims is not None:
        _token_cache[token] = (now + 60, claims)
        return claims

    return None
