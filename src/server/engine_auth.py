"""Mint short-lived WHEP capability tokens for the C++ engine.

A compact HMAC scheme that must byte-match the engine's verifier:
`"{expiry}.{instance_name}.{hmac_sha256_hex}"`, HMAC-SHA256 over the WHEP
secret.
"""

import hashlib
import hmac
import threading
import time
from typing import Callable


class EngineTokenIssuer:
    def __init__(
        self,
        whep_secret: str,
        whep_ttl_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ):
        if not whep_secret:
            raise ValueError("whep_secret must not be empty")
        self._whep_secret = whep_secret
        self._whep_ttl_seconds = whep_ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._last_whep_expiry = 0

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
