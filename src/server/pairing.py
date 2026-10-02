"""Device pairing: a short-lived code shown on the PC is exchanged once for
a long-lived device token. Only the token's SHA-256 digest is stored."""

import hashlib
import hmac
import json
import logging
import os
import secrets
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Callable

log = logging.getLogger(__name__)

CODE_TTL_SECONDS = 300
MAX_FAILED_ATTEMPTS = 5
_MAX_NAME_LENGTH = 64
_MAX_TOKEN_LENGTH = 256
_DEVICE_STRING_FIELDS = ("id", "name", "token_sha256")


@dataclass(frozen=True)
class PairedDevice:
    id: str
    name: str
    created_at: float


@dataclass(frozen=True)
class PairingWindow:
    generation: int
    expires_at: float


def bearer_token(authorization: str | None) -> str | None:
    """Return one exact Bearer credential, rejecting every other form."""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization[7:]
    if not token or token.strip() != token or " " in token:
        return None
    return token


def default_store_path() -> str:
    if sys.platform == "win32":
        directory = r"C:\ProgramData\EmuCtrl"
    else:
        directory = os.path.join(os.path.expanduser("~"), ".emuctrl")
    return os.path.join(directory, "paired_devices.json")


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class PairingStore:
    def __init__(self, path: str | None = None,
                 clock: Callable[[], float] = time.time):
        self._path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._code: str | None = None
        self._code_expires_at = 0.0
        self._failed_attempts = 0
        self._window_generation = 0
        self._window_closed: list[Callable[[int], None]] = []
        self._last_save_ok = True
        self._devices: list[dict] = self._load()

    @property
    def last_save_ok(self) -> bool:
        """False when the latest write failed: changes then die with the process."""
        return self._last_save_ok

    # -- pairing code ------------------------------------------------------

    def start_pairing(self) -> str:
        with self._lock:
            previous = self._window_generation if self._code is not None else None
            self._window_generation += 1
            self._code = f"{secrets.randbelow(1_000_000):06d}"
            self._code_expires_at = self._clock() + CODE_TTL_SECONDS
            self._failed_attempts = 0
            code = self._code
        self._notify_window_closed(previous)
        return code

    def active_code(self) -> tuple[str, int] | None:
        with self._lock:
            previous = self._window_generation if self._code is not None else None
            code = self._active_code_locked()
            result = None if code is None else (code, int(self._code_expires_at - self._clock()))
        if result is None:
            self._notify_window_closed(previous)
        return result

    def pairing_window(self) -> PairingWindow | None:
        """Snapshot for invitations; generation changes even if a code repeats."""
        with self._lock:
            previous = self._window_generation if self._code is not None else None
            code = self._active_code_locked()
            result = None if code is None else PairingWindow(self._window_generation, self._code_expires_at)
        if result is None:
            self._notify_window_closed(previous)
        return result

    def on_window_closed(self, callback: Callable[[int], None]) -> Callable[[], None]:
        """Observe replacement, expiry, consumption and exhausted attempts.

        Observers run outside the store lock and must only enqueue I/O.
        """
        with self._lock:
            self._window_closed.append(callback)

        def unsubscribe() -> None:
            with self._lock:
                if callback in self._window_closed:
                    self._window_closed.remove(callback)

        return unsubscribe

    def _notify_window_closed(self, generation: int | None) -> None:
        if generation is None:
            return
        with self._lock:
            callbacks = list(self._window_closed)
        for callback in callbacks:
            try:
                callback(generation)
            except Exception:
                log.warning("pairing: remote window observer failed")

    def pair(self, code: str, device_name: str, *, window_generation: int | None = None) -> str | None:
        if not isinstance(code, str):
            return None
        candidate = "".join(code.split())
        closed = None
        try:
            with self._lock:
                generation = self._window_generation
                was_active = self._code is not None
                active = self._active_code_locked()
                if was_active and active is None:
                    closed = generation
                if active is None or (window_generation is not None and window_generation != generation):
                    return None
                if not hmac.compare_digest(candidate.encode("utf-8"), active.encode("utf-8")):
                    self._failed_attempts += 1
                    if self._failed_attempts >= MAX_FAILED_ATTEMPTS:
                        self._code = None
                        closed = generation
                    return None
                self._code = None
                closed = generation
                token = secrets.token_urlsafe(32)
                name = (device_name or "").strip()[:_MAX_NAME_LENGTH] or "Device"
                self._devices.append({
                    "id": uuid.uuid4().hex[:8],
                    "name": name,
                    "created_at": self._clock(),
                    "token_sha256": _digest(token),
                })
                self._last_save_ok = self._save_locked()
                return token
        finally:
            self._notify_window_closed(closed)

    # -- device tokens -----------------------------------------------------

    def is_valid_token(self, token: str | None) -> bool:
        return self.device_for_token(token) is not None

    def device_for_token(self, token: str | None) -> PairedDevice | None:
        if not isinstance(token, str) or not token or len(token) > _MAX_TOKEN_LENGTH:
            return None
        digest = _digest(token)
        with self._lock:
            for device in self._devices:
                if hmac.compare_digest(digest, device["token_sha256"]):
                    return PairedDevice(device["id"], device["name"], device["created_at"])
        return None

    def list_devices(self) -> list[PairedDevice]:
        with self._lock:
            return [
                PairedDevice(d["id"], d["name"], d["created_at"])
                for d in self._devices
            ]

    def remove_device(self, device_id: str) -> bool:
        with self._lock:
            remaining = [d for d in self._devices if d["id"] != device_id]
            if len(remaining) == len(self._devices):
                return False
            self._devices = remaining
            self._last_save_ok = self._save_locked()
            return True

    def remove_all(self) -> None:
        with self._lock:
            self._devices = []
            self._last_save_ok = self._save_locked()

    # -- internals ---------------------------------------------------------

    def _active_code_locked(self) -> str | None:
        if self._code is None:
            return None
        if self._clock() >= self._code_expires_at:
            self._code = None
            return None
        return self._code

    def _load(self) -> list[dict]:
        if not self._path:
            return []
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                devices = json.load(f)["devices"]
        except FileNotFoundError:
            return []
        except Exception:
            log.warning("pairing: %s is unreadable; starting with no paired devices", self._path)
            return []
        if not isinstance(devices, list):
            return []
        return [
            d for d in devices
            if isinstance(d, dict)
            and all(isinstance(d.get(key), str) for key in _DEVICE_STRING_FIELDS)
            and isinstance(d.get("created_at"), (int, float))
        ]

    def _save_locked(self) -> bool:
        if not self._path:
            return True
        directory = os.path.dirname(self._path)
        tmp_path = None
        try:
            os.makedirs(directory, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(prefix=".paired_devices.", suffix=".tmp", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"devices": self._devices}, f)
                f.flush()
                os.fsync(f.fileno())
            # Atomic on POSIX and Windows: the file holds the old list or the
            # new one, never a half-written one.
            os.replace(tmp_path, self._path)
            tmp_path = None
            return True
        except Exception:
            log.warning("pairing: could not write %s; pairings and revocations will not survive a restart", self._path)
            return False
        finally:
            if tmp_path is not None:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
