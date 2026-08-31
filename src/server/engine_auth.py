"""Mint short-lived WHEP capability tokens and signaling JWTs for the C++ engine.

WHEP tokens are a compact HMAC scheme that must byte-match the C++ engine's
verifier: `"{expiry}.{instance_name}"` signed with HMAC-SHA256 over the WHEP
secret, hex-encoded, appended after a final dot.

Signaling tokens are compact HS256 JWTs (UTF-8, unpadded base64url, minified
JSON) verified by the VPS Node.js relay (`infra/vps/signaling/server.js`)
using the `jsonwebtoken` package. An empty signaling secret disables signing
entirely (trusted local/dev relay) and callers get back an empty string.
"""

import base64
import hashlib
import hmac
import json
import time
from typing import Callable, Literal

_VALID_ROLES = ("engine", "viewer")


def _b64url_no_pad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class EngineTokenIssuer:
    def __init__(
        self,
        whep_secret: str,
        signaling_secret: str = "",
        whep_ttl_seconds: int = 300,
        viewer_ttl_seconds: int = 300,
        engine_ttl_seconds: int = 604800,
        clock: Callable[[], float] = time.time,
    ):
        if not whep_secret:
            raise ValueError("whep_secret must not be empty")
        self._whep_secret = whep_secret
        self._signaling_secret = signaling_secret
        self._whep_ttl_seconds = whep_ttl_seconds
        self._viewer_ttl_seconds = viewer_ttl_seconds
        self._engine_ttl_seconds = engine_ttl_seconds
        self._clock = clock

    def whep(self, instance_name: str) -> str:
        expiry = int(self._clock()) + self._whep_ttl_seconds
        payload = f"{expiry}.{instance_name}"
        signature = hmac.new(
            self._whep_secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        return f"{payload}.{signature}"

    def signaling(self, instance_name: str, role: Literal["engine", "viewer"]) -> str:
        if role not in _VALID_ROLES:
            raise ValueError(f"invalid role: {role!r}")

        if not self._signaling_secret:
            return ""

        # NOTE: engine tokens are documented to use the separate 7-day
        # engine_ttl_seconds lifetime, but the brief's own literal test
        # (test_signaling_payload_contains_session_role_and_expiry) calls
        # signaling("instance0", "engine") with default TTLs and asserts
        # exp == 1300 (i.e. viewer_ttl_seconds, not engine_ttl_seconds).
        # That test is specified as verbatim/authoritative, so this method
        # currently uses viewer_ttl_seconds for all roles. See task-3-report.md
        # "Concerns" for the conflict with the prose spec; engine_ttl_seconds
        # is still accepted and stored so the constructor signature matches
        # the brief, but is not yet consumed here.
        expiry = int(self._clock()) + self._viewer_ttl_seconds

        header = {"alg": "HS256", "typ": "JWT"}
        payload = {"session": instance_name, "role": role, "exp": expiry}

        header_b64 = _b64url_no_pad(
            json.dumps(header, separators=(",", ":")).encode("utf-8")
        )
        payload_b64 = _b64url_no_pad(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )
        signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
        signature = _b64url_no_pad(
            hmac.new(
                self._signaling_secret.encode(), signing_input, hashlib.sha256
            ).digest()
        )
        return f"{header_b64}.{payload_b64}.{signature}"
