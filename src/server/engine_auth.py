"""Mint short-lived WHEP capability tokens and a long-lived, Ed25519-signed
engine registration token for the C++ engine.

WHEP tokens are a compact HMAC scheme that must byte-match the C++ engine's
verifier: `"{expiry}.{instance_name}.{hmac_sha256_hex}"` signed with
HMAC-SHA256 over the WHEP secret. Local/LAN-only path, never touches the
shared relay -- unaffected by anything below.

Engine registration tokens are compact JWTs (UTF-8, unpadded base64url,
minified JSON), signed with this install's own Ed25519 private key
(src/server/install_identity.py) instead of a secret shared across every
install on the relay -- see docs/superpowers/specs/2026-09-04-public-session-isolation-design.md.
The VPS relay (infra/vps/signaling/server.js) verifies the signature against
the public key registered for the session's account in Supabase's
`installs` table. A missing private key (signaling_private_key=None)
disables signing entirely (trusted local/dev relay) and callers get back an
empty string.
"""

import base64
import hashlib
import hmac
import json
import threading
import time
from typing import Callable

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _b64url_no_pad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


class EngineTokenIssuer:
    def __init__(
        self,
        whep_secret: str,
        signaling_private_key: Ed25519PrivateKey | None = None,
        whep_ttl_seconds: int = 300,
        engine_ttl_seconds: int = 604800,
        clock: Callable[[], float] = time.time,
    ):
        if not whep_secret:
            raise ValueError("whep_secret must not be empty")
        self._whep_secret = whep_secret
        self._signaling_private_key = signaling_private_key
        self._whep_ttl_seconds = whep_ttl_seconds
        self._engine_ttl_seconds = engine_ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._last_whep_expiry = 0
        self._jwt_issuance = 0

    def whep(self, instance_name: str) -> str:
        with self._lock:
            expiry = max(
                int(self._clock()) + self._whep_ttl_seconds,
                self._last_whep_expiry + 1,
            )
            self._last_whep_expiry = expiry
        payload = f"{expiry}.{instance_name}"
        signature = hmac.new(
            self._whep_secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        return f"{payload}.{signature}"

    def engine_token(self, session: str) -> str:
        """Sign a registration token for `session` (the full relay session
        id, e.g. "{owner_user_id}.{instance_name}"). Role is always
        "engine" -- viewers now present their own Supabase access token to
        the relay directly, they no longer need one of these."""
        if self._signaling_private_key is None:
            return ""

        expiry = int(self._clock()) + self._engine_ttl_seconds

        header = {"alg": "EdDSA", "typ": "JWT"}
        with self._lock:
            self._jwt_issuance += 1
            jti = str(self._jwt_issuance)
        payload = {"session": session, "role": "engine", "exp": expiry, "jti": jti}

        header_b64 = _b64url_no_pad(
            json.dumps(header, separators=(",", ":")).encode("utf-8")
        )
        payload_b64 = _b64url_no_pad(
            json.dumps(payload, separators=(",", ":")).encode("utf-8")
        )
        signing_input = f"{header_b64}.{payload_b64}".encode("utf-8")
        signature = _b64url_no_pad(self._signaling_private_key.sign(signing_input))
        return f"{header_b64}.{payload_b64}.{signature}"
