"""Owner-only installation identity storage and bounded broker registration."""

import base64
import ctypes
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from broker.identity_store import InstallationIdentity


class RemoteIdentityError(RuntimeError):
    """Remote identity is unavailable; local device pairing remains usable."""


def service_origin(url: str, *, allow_insecure_localhost: bool = False) -> str:
    try:
        parts = urlsplit(url)
        allowed_http = (allow_insecure_localhost and parts.scheme == "http"
                        and parts.hostname in ("localhost", "127.0.0.1", "::1"))
        if (not parts.hostname or (parts.scheme != "https" and not allowed_http)
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or parts.path not in ("", "/")):
            raise ValueError
        parts.port  # Reject malformed ports before registration.
        return f"{parts.scheme}://{parts.netloc}"
    except (ValueError, TypeError, AttributeError):
        raise RemoteIdentityError("Remote service must use a trusted HTTPS origin") from None


def default_identity_path() -> Path:
    if sys.platform == "win32":
        directory = os.environ.get("LOCALAPPDATA")
        if not directory:
            raise RemoteIdentityError("Windows user data directory is unavailable")
        return Path(directory) / "EmuCtrl" / "remote_identity.json"
    return Path.home() / ".emuctrl" / "remote_identity.json"


class DPAPIProtector:
    """Current-user DPAPI, without prompts or machine-wide protection."""

    def _crypt(self, data: bytes, *, decrypt: bool) -> bytes:
        if sys.platform != "win32":
            raise RemoteIdentityError("Remote identity requires Windows DPAPI")

        class Blob(ctypes.Structure):
            _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]

        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        operation = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
        operation.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32,
                              ctypes.POINTER(Blob)]
        operation.restype = ctypes.c_int
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        buffer = (ctypes.c_ubyte * len(data)).from_buffer_copy(data)
        source = Blob(len(data), buffer)
        output = Blob()
        # CRYPTPROTECT_UI_FORBIDDEN; deliberately omit LOCAL_MACHINE.
        if not operation(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
            raise RemoteIdentityError("Windows identity protection failed")
        try:
            return ctypes.string_at(output.data, output.size)
        finally:
            kernel32.LocalFree(output.data)

    def protect(self, data: bytes) -> bytes:
        return self._crypt(data, decrypt=False)

    def unprotect(self, data: bytes) -> bytes:
        return self._crypt(data, decrypt=True)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _http_json(method: str, url: str, payload: dict, timeout: float) -> dict:
    request = Request(url, data=json.dumps(payload).encode("utf-8"), method=method,
                      headers={"Content-Type": "application/json", "Accept": "application/json"})
    # In particular, never forward a reset credential to a redirected origin.
    with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
        body = response.read(16385)
        if len(body) > 16384:
            raise RemoteIdentityError("Remote identity response is too large")
        return json.loads(body)


class RemoteIdentityStore:
    def __init__(self, path: str | Path | None = None, *, protector=None,
                 request=None, allow_insecure_localhost: bool = False):
        self._path = Path(path) if path is not None else default_identity_path()
        self._protector = protector if protector is not None else DPAPIProtector()
        self._request = request if request is not None else _http_json
        self._allow_insecure_localhost = allow_insecure_localhost
        self._lock = threading.Lock()

    def load_or_register(self, service_url: str) -> InstallationIdentity:
        origin = service_origin(service_url, allow_insecure_localhost=self._allow_insecure_localhost)
        with self._lock:
            saved = self._load()
            if saved is not None:
                identity, saved_origin = saved
                if saved_origin != origin:
                    raise RemoteIdentityError("Stored identity belongs to a different remote service")
                return identity
            return self._register(origin)

    def reset_identity(self) -> InstallationIdentity:
        with self._lock:
            saved = self._load()
            if saved is None:
                raise RemoteIdentityError("No stored identity to reset")
            identity, origin = saved
            try:
                result = self._request("DELETE", f"{origin}/installations/{identity.installation_id}",
                                       {"credential": identity.credential}, 10.0)
                if result.get("ok") is not True:
                    raise ValueError
            except Exception:
                raise RemoteIdentityError("Could not revoke remote identity; reset requires an online service") from None
            try:
                self._path.unlink()
            except OSError:
                raise RemoteIdentityError("Could not remove revoked remote identity") from None
            return self._register(origin)

    @staticmethod
    def _identity(data: dict) -> InstallationIdentity:
        identity = InstallationIdentity.model_validate(data)
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identity.installation_id)
                or not identity.credential or len(identity.credential) > 4096):
            raise ValueError("Invalid identity")
        return identity

    def _load(self) -> tuple[InstallationIdentity, str] | None:
        try:
            text = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except Exception:
            raise RemoteIdentityError("Could not read stored remote identity") from None
        try:
            envelope = json.loads(text)
            protected = base64.b64decode(envelope["protected"], validate=True)
            saved = json.loads(self._protector.unprotect(protected))
            identity = self._identity(saved["identity"])
            origin = service_origin(saved["service_url"], allow_insecure_localhost=self._allow_insecure_localhost)
            return identity, origin
        except Exception:
            raise RemoteIdentityError("Could not decrypt or validate stored remote identity") from None

    def _register(self, origin: str) -> InstallationIdentity:
        try:
            identity = self._identity(self._request("POST", origin + "/installations", {}, 10.0))
        except Exception:
            raise RemoteIdentityError("Could not register remote identity") from None
        self._save(identity, origin)
        return identity

    def _save(self, identity: InstallationIdentity, origin: str) -> None:
        temporary = None
        try:
            plaintext = json.dumps({"identity": identity.model_dump(), "service_url": origin}).encode("utf-8")
            protected = self._protector.protect(plaintext)
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".remote_identity.", dir=self._path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                if os.name != "nt":
                    os.fchmod(output.fileno(), 0o600)
                json.dump({"protected": base64.b64encode(protected).decode("ascii")}, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self._path)
            temporary = None
        except Exception:
            raise RemoteIdentityError("Could not persist protected remote identity") from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
