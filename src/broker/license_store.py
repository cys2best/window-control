import fcntl
import hashlib
import json
import math
import os
import re
import secrets
import tempfile
import threading
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

MOVE_COOLDOWN_SECONDS = 604800

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_LOOKALIKES = str.maketrans({"O": "0", "I": "1", "L": "1"})
_RECORD_KEYS = {"installation_id", "issued_at", "bound_at", "moved_at",
                "expires_at", "revoked", "note"}


class LicenseStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class Activation:
    status: Literal["bound", "moved", "in_use", "invalid"]
    previous_installation: str | None = None
    retry_after: int | None = None


@dataclass(frozen=True)
class LicenseView:
    digest_prefix: str
    installation_id: str | None
    issued_at: int
    bound_at: int | None
    moved_at: int | None
    expires_at: int | None
    revoked: bool
    note: str


def normalize_key(raw: str) -> str:
    if not isinstance(raw, str):
        raise ValueError("invalid license key")
    key = raw.upper().replace(" ", "").replace("-", "")
    if key.startswith("EMU"):
        key = key[3:]
    key = key.translate(_LOOKALIKES)
    if len(key) != 20 or any(ch not in _ALPHABET for ch in key):
        raise ValueError("invalid license key")
    return key


def generate_key() -> str:
    value = secrets.randbits(100)
    chars = "".join(_ALPHABET[(value >> (5 * i)) & 31] for i in range(20))
    return "EMU-" + "-".join(chars[i:i + 5] for i in range(0, 20, 5))


def _digest(normalized: str) -> str:
    return hashlib.sha256(normalized.encode()).hexdigest()


def _is_time(value, *, optional: bool) -> bool:
    if value is None:
        return optional
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _valid_record(digest, record) -> bool:
    return (isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest) is not None
            and isinstance(record, dict) and set(record) == _RECORD_KEYS
            and (record["installation_id"] is None
                 or (isinstance(record["installation_id"], str)
                     and re.fullmatch(r"[0-9a-f]{32}", record["installation_id"]) is not None))
            and _is_time(record["issued_at"], optional=False)
            and _is_time(record["bound_at"], optional=True)
            and _is_time(record["moved_at"], optional=True)
            and _is_time(record["expires_at"], optional=True)
            and isinstance(record["revoked"], bool)
            and isinstance(record["note"], str))


def _usable(record: dict, now: float) -> bool:
    return not record["revoked"] and (record["expires_at"] is None or now < record["expires_at"])


class LicenseStore:
    """Relay license keys, shared by the broker and the admin CLI through one file lock."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()

    @contextmanager
    def _transaction(self):
        with self._lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                lock_file = open(f"{self.path}.lock", "a")
            except OSError:
                raise LicenseStoreError("license storage unavailable") from None
            with lock_file:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                try:
                    yield self._read_data()
                finally:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _read_data(self) -> dict:
        try:
            raw = self.path.read_text()
        except FileNotFoundError:
            return {}
        except (OSError, UnicodeError):
            raise LicenseStoreError("license storage unavailable") from None
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            raise LicenseStoreError("license storage invalid") from None
        if not isinstance(data, dict) or not all(_valid_record(d, r) for d, r in data.items()):
            raise LicenseStoreError("license storage invalid")
        return data

    def _write_data(self, data: dict):
        tmp_path = None
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(dir=self.path.parent)
            with os.fdopen(tmp_fd, "w") as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, self.path)
        except OSError:
            raise LicenseStoreError("license storage unavailable") from None
        finally:
            if tmp_path is not None:
                with suppress(OSError):
                    os.unlink(tmp_path)

    @staticmethod
    def _find(data: dict, key: str) -> str | None:
        try:
            digest = _digest(normalize_key(key))
        except ValueError:
            return None
        return digest if digest in data else None

    def issue(self, *, now: float, days: int | None = None, note: str = "") -> str:
        with self._transaction() as data:
            while True:
                key = generate_key()
                digest = _digest(normalize_key(key))
                if digest not in data:
                    break
            data[digest] = {
                "installation_id": None,
                "issued_at": now,
                "bound_at": None,
                "moved_at": None,
                "expires_at": None if days is None else now + days * 86400,
                "revoked": False,
                "note": note,
            }
            self._write_data(data)
            return key

    def activate(self, key: str, installation_id: str, *, now: float,
                 installation_exists: Callable[[str], bool]) -> Activation:
        with self._transaction() as data:
            digest = self._find(data, key)
            if digest is None or not _usable(data[digest], now):
                return Activation("invalid")
            record = data[digest]
            current = record["installation_id"]
            if current == installation_id:
                return Activation("bound")
            if current is None or not installation_exists(current):
                record.update(installation_id=installation_id, bound_at=now)
                self._write_data(data)
                return Activation("bound")
            moved_at = record["moved_at"]
            if moved_at is not None and now - moved_at < MOVE_COOLDOWN_SECONDS:
                return Activation("in_use", retry_after=math.ceil(moved_at + MOVE_COOLDOWN_SECONDS - now))
            record.update(installation_id=installation_id, bound_at=now, moved_at=now)
            self._write_data(data)
            return Activation("moved", previous_installation=current)

    def deactivate(self, installation_id: str) -> bool:
        with self._transaction() as data:
            changed = False
            for record in data.values():
                if record["installation_id"] == installation_id:
                    record.update(installation_id=None, bound_at=None, moved_at=None)
                    changed = True
            if changed:
                self._write_data(data)
            return changed

    def is_licensed(self, installation_id: str, *, now: float) -> bool:
        try:
            with self._transaction() as data:
                return any(record["installation_id"] == installation_id and _usable(record, now)
                           for record in data.values())
        except LicenseStoreError:
            return False

    def reset(self, key: str) -> bool:
        with self._transaction() as data:
            digest = self._find(data, key)
            if digest is None:
                return False
            data[digest].update(installation_id=None, bound_at=None, moved_at=None)
            self._write_data(data)
            return True

    def revoke(self, key: str) -> bool:
        with self._transaction() as data:
            digest = self._find(data, key)
            if digest is None:
                return False
            data[digest]["revoked"] = True
            self._write_data(data)
            return True

    def list(self) -> list[LicenseView]:
        with self._transaction() as data:
            return [LicenseView(digest_prefix=digest[:8], **record) for digest, record in data.items()]
