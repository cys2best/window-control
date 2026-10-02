"""Ephemeral remote invitations bound to the PC's existing pairing window."""

from dataclasses import dataclass
import hmac
import logging
import secrets
import threading
from typing import Callable

from broker.identity_store import InstallationIdentity
from remote_protocol import ErrorCode
from server.pairing import PairingStore, PairingWindow
from server.remote_identity import service_origin

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PairingInvitation:
    handle: str
    expires_at: float
    url: str


class RemotePairingError(RuntimeError):
    def __init__(self, code: ErrorCode, message: str):
        super().__init__(message)
        self.code = code


class RemotePairing:
    def __init__(self, pairing: PairingStore, identity: InstallationIdentity,
                 service_url: str, *, publish: Callable[[PairingInvitation], None] | None = None,
                 close: Callable[[str], None] | None = None,
                 on_error: Callable[[RemotePairingError], None] | None = None,
                 allow_insecure_localhost: bool = False):
        """publish/close/on_error enqueue host-channel work without I/O.

        The host lifecycle calls set_online(True) after authenticated WSS
        connection and set_online(False) on disconnect. A fresh owner action
        is required after disconnect; invitations are never resurrected.
        """
        self._pairing = pairing
        self._identity = identity
        self._origin = service_origin(service_url, allow_insecure_localhost=allow_insecure_localhost)
        self._publish = publish
        self._close = close
        self._on_error = on_error
        self._last_error: RemotePairingError | None = None
        self._lock = threading.RLock()
        self._online = False
        self._stopped = False
        self._invitation: PairingInvitation | None = None
        self._window: PairingWindow | None = None
        self._unsubscribe = pairing.on_window_closed(self._window_closed)

    def set_online(self, online: bool) -> None:
        with self._lock:
            if self._stopped and online:
                raise RemotePairingError(ErrorCode.UNAVAILABLE, "Remote pairing has stopped")
            self._online = bool(online)
            if online:
                self._last_error = None
            if not online:
                self._clear()

    @property
    def last_error(self) -> RemotePairingError | None:
        """Closure failure retained until authenticated readiness is restored."""
        with self._lock:
            return self._last_error

    def shutdown(self) -> None:
        """Detach this manager when stopping/replacing the host client."""
        try:
            with self._lock:
                self._stopped = True
                self._online = False
                self._clear()
        finally:
            self._unsubscribe()

    def open(self, *, window_generation: int | None = None) -> PairingInvitation:
        with self._lock:
            if not self._online:
                raise RemotePairingError(ErrorCode.OFFLINE, "Remote host is offline")
            window = self._pairing.pairing_window()
            if window is None or (window_generation is not None and window_generation != window.generation):
                raise RemotePairingError(ErrorCode.EXPIRED_PAIRING, "Open a pairing code on the PC first")
            active = self.active_invitation()
            if not self._online:
                raise RemotePairingError(ErrorCode.OFFLINE, "Remote host is offline")
            if active is not None:
                return active
            handle = secrets.token_urlsafe(32)
            invitation = PairingInvitation(handle, window.expires_at, self._origin + "/pair#invite=" + handle)
            self._invitation = invitation
            self._window = window
            try:
                if self._publish is not None:
                    self._publish(invitation)
            except Exception:
                self._clear()
                raise RemotePairingError(ErrorCode.UNAVAILABLE, "Could not publish remote invitation") from None
            # A local operation can replace the window while publication is
            # queued; do not return a handle for that previous window.
            if self.active_invitation() is None:
                raise RemotePairingError(ErrorCode.EXPIRED_PAIRING, "Pairing window has closed")
            return invitation

    def active_invitation(self) -> PairingInvitation | None:
        with self._lock:
            if self._invitation is not None and self._pairing.pairing_window() != self._window:
                self._clear()
            return self._invitation

    def submit(self, handle: str, code: str, device_name: str) -> dict[str, str]:
        with self._lock:
            if not self._online:
                raise RemotePairingError(ErrorCode.OFFLINE, "Remote host is offline")
            invitation = self.active_invitation()
            if (invitation is None or not isinstance(handle, str)
                    or not hmac.compare_digest(handle.encode("utf-8"), invitation.handle.encode("utf-8"))):
                raise RemotePairingError(ErrorCode.EXPIRED_PAIRING, "Remote invitation has closed")
            if not isinstance(code, str) or not isinstance(device_name, str):
                raise RemotePairingError(ErrorCode.INVALID_REQUEST, "Invalid pairing request")
            token = self._pairing.pair(code, device_name, window_generation=self._window.generation)
            if token is None:
                raise RemotePairingError(ErrorCode.NOT_PAIRED, "Pairing code was not accepted")
            if not self._pairing.last_save_ok:
                raise RemotePairingError(ErrorCode.UNAVAILABLE, "Could not persist remote device pairing")
            return {"installation_id": self._identity.installation_id, "token": token}

    def _window_closed(self, generation: int) -> None:
        with self._lock:
            if self._window is not None and self._window.generation == generation:
                self._clear()

    def _clear(self) -> None:
        invitation = self._invitation
        self._invitation = None
        self._window = None
        if invitation is not None and self._close is not None:
            try:
                self._close(invitation.handle)
            except Exception:
                # Local invalidation remains authoritative, but rejected
                # closure means the broker channel is no longer usable.
                self._online = False
                self._last_error = RemotePairingError(ErrorCode.UNAVAILABLE, "Could not close remote invitation")
                if self._on_error is not None:
                    try:
                        self._on_error(self._last_error)
                    except Exception:
                        # Lifecycle can still inspect last_error if even its
                        # notification queue rejects work. Local pairing stays usable.
                        log.warning("remote pairing: closure error notification failed")
