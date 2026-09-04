"""Per-install identity: a locally-persisted Ed25519 keypair that proves
"this specific PC" to the public signaling relay, and a cache of which
Supabase account most recently authenticated against this install.

Neither the private key nor the owner cache is ever transmitted anywhere.
Only the *public* key half is uploaded (once per login) to Supabase's
`installs` table (see src/server/supabase_client.py) -- the private key
never leaves this machine.
"""

import base64
import os

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_CANDIDATE_DIRS = [
    r"C:\ProgramData\WindowControl", r"C:\Windows\Temp", r"C:\Temp", "/tmp",
]
_KEY_FILENAME = "install_key.bin"
_OWNER_FILENAME = "install_owner.txt"


def _b64url_no_pad(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _read_first_existing(filename: str) -> bytes | None:
    for directory in _CANDIDATE_DIRS:
        path = os.path.join(directory, filename)
        try:
            with open(path, "rb") as f:
                return f.read()
        except Exception:
            continue
    return None


def _write_first_writable(filename: str, data: bytes) -> bool:
    for directory in _CANDIDATE_DIRS:
        try:
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, filename)
            with open(path, "wb") as f:
                f.write(data)
            return True
        except Exception:
            continue
    return False


def get_or_create_install_keypair() -> tuple[Ed25519PrivateKey, str]:
    """Return (private_key, base64url-no-pad public key).

    Persists the private key the first time it's called; every later call,
    in this or a future process, reads the same one back. Falls back to an
    in-memory keypair (not persisted) if no candidate directory is
    writable -- the public path just won't survive a restart until a
    writable path exists.
    """
    existing = _read_first_existing(_KEY_FILENAME)
    if existing is not None:
        private_key = Ed25519PrivateKey.from_private_bytes(existing)
    else:
        private_key = Ed25519PrivateKey.generate()
        raw = private_key.private_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PrivateFormat.Raw,
            encryption_algorithm=serialization.NoEncryption(),
        )
        _write_first_writable(_KEY_FILENAME, raw)

    public_raw = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_key, _b64url_no_pad(public_raw)


def get_cached_owner_user_id() -> str | None:
    raw = _read_first_existing(_OWNER_FILENAME)
    if raw is None:
        return None
    value = raw.decode("utf-8").strip()
    return value or None


def set_cached_owner_user_id(user_id: str) -> None:
    _write_first_writable(_OWNER_FILENAME, user_id.encode("utf-8"))
