"""Shared-token auth gate for exposing the app past a trusted LAN.

Set AUTH_TOKEN (env var) to require it. The browser exchanges the token once
for a signed, time-limited session cookie (POST /login) so the token itself
never needs to travel with every request. Unset AUTH_TOKEN and every check
here is a no-op — LAN-only deployments are unaffected.
"""

import hashlib
import hmac
import time

import config

COOKIE_NAME = "wc_session"
SESSION_MAX_AGE_SECONDS = 30 * 24 * 3600  # 30 days


def auth_enabled() -> bool:
    return bool(config.AUTH_TOKEN)


def check_token(token: str) -> bool:
    if not auth_enabled():
        return False
    return hmac.compare_digest(token or "", config.AUTH_TOKEN)


def _sign(issued_at: str) -> str:
    return hmac.new(
        config.AUTH_TOKEN.encode(), issued_at.encode(), hashlib.sha256
    ).hexdigest()


def make_session_cookie(issued_at: float | None = None) -> str:
    ts = str(int(issued_at if issued_at is not None else time.time()))
    return f"{ts}.{_sign(ts)}"


def verify_session_cookie(cookie: str | None) -> bool:
    if not auth_enabled() or not cookie:
        return False
    ts, _, sig = cookie.partition(".")
    if not ts or not sig:
        return False
    if not hmac.compare_digest(sig, _sign(ts)):
        return False
    try:
        issued_at = int(ts)
    except ValueError:
        return False
    return time.time() - issued_at <= SESSION_MAX_AGE_SECONDS
