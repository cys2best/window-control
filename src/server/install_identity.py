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
import tempfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_CANDIDATE_DIRS = [
    r"C:\ProgramData\EmuCtrl", r"C:\ProgramData\WindowControl", r"C:\Windows\Temp", r"C:\Temp", "/tmp",
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
    """Write `data` to `directory/filename` atomically.

    A plain truncating write can be interrupted (crash, power loss, kill)
    partway through, leaving a short or zero-length file that the next boot
    reads back as present-but-corrupt. For the keypair that used to mean an
    unhandled exception out of get_or_create_install_keypair() and a dead
    app. So: write a uniquely-named temp file in the *same* directory (a
    rename is only atomic within one filesystem), fsync it so the bytes are
    really on disk, then os.replace() it onto the destination -- atomic on
    both POSIX and Windows. The destination therefore only ever holds the
    complete old value or the complete new one.
    """
    tmp_path = None
    try:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, filename)
        fd, tmp_path = tempfile.mkstemp(prefix=f".{filename}.", suffix=".tmp", dir=directory)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        tmp_path = None  # ownership transferred; nothing left to clean up
        return True
    except Exception:
        return False
    finally:
        if tmp_path is not None:
            # The swap never happened -- don't litter the directory with a
            # temp file that no later call would ever clean up.
            try:
                os.remove(tmp_path)
            except Exception:
                pass


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

    An unusable existing key file (corrupt, truncated, wrong length) is
    treated exactly like a missing one: a fresh keypair is generated and
    written over it. Raising here would propagate through create_app() /
    build_engine_orchestrator() and kill app startup outright, which is
    the opposite of the graceful degradation this module promises.
    """
    private_key = None
    existing = _read_first_existing(_KEY_FILENAME)
    if existing is not None:
        try:
            private_key = Ed25519PrivateKey.from_private_bytes(existing)
        except Exception:
            private_key = None

    if private_key is None:
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
