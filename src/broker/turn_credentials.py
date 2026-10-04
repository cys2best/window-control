"""Coturn REST credentials. The signing secret never leaves the broker."""

import base64
from dataclasses import dataclass, field
import hashlib
import hmac
import math
import re


@dataclass(frozen=True)
class IceBundle:
    username: str
    credential: str = field(repr=False)
    expires_at: int
    renew_after: int


def issue_turn_credentials(secret, installation_id, session_id, endpoint, now, ttl=3600) -> IceBundle:
    """Called only after authenticated PC admission, separately for each end."""
    if (not secret or endpoint not in {"host", "viewer"}
            or type(ttl) is not int or not 300 < ttl <= 3600
            or not math.isfinite(now) or now < 0
            or any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", part)
                   for part in (installation_id, session_id))):
        raise ValueError("invalid TURN credential scope")
    expiry = int(now) + ttl
    username = f"{expiry}:{installation_id}:{session_id}:{endpoint}"
    credential = base64.b64encode(hmac.new(secret.encode(), username.encode(), hashlib.sha1).digest()).decode("ascii")
    return IceBundle(username, credential, expiry, ttl - 300)
