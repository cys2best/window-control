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


def _find_existing_dir(filename: str) -> str | None:
    """Return the highest-priority candidate directory that currently holds
    a readable copy of `filename`, or None if no candidate has one.
    """
    for directory in _CANDIDATE_DIRS:
        path = os.path.join(directory, filename)
        try:
            with open(path, "rb"):
                return directory
        except Exception:
            continue
    return None


def _write_to_dir(directory: str, filename: str, data: bytes) -> bool:
    try:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, filename)
        with open(path, "wb") as f:
            f.write(data)
        return True
    except Exception:
        return False


def _write_first_writable(filename: str, data: bytes) -> str | None:
    for directory in _CANDIDATE_DIRS:
        if _write_to_dir(directory, filename, data):
            return directory
    return None


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
    """Write the cached owner user id, keeping it in the same directory a
    later get_cached_owner_user_id() call will read from.

    If an existing copy is found and its directory is still writable, the
    new value overwrites it in place -- the common case. Otherwise, falls
    back to writing the first writable candidate directory (in priority
    order) and, best-effort, deletes any stale copy(ies) left behind in
    higher-priority directories, so a subsequent read doesn't pick up the
    old value first.
    """
    data = user_id.encode("utf-8")

    existing_dir = _find_existing_dir(_OWNER_FILENAME)
    if existing_dir is not None and _write_to_dir(existing_dir, _OWNER_FILENAME, data):
        return

    written_dir = _write_first_writable(_OWNER_FILENAME, data)
    if written_dir is None:
        return

    for directory in _CANDIDATE_DIRS:
        if directory == written_dir:
            break
        try:
            os.remove(os.path.join(directory, _OWNER_FILENAME))
        except Exception:
            pass
