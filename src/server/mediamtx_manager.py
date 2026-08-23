"""
mediamtx process manager.

Starts mediamtx.exe with a generated config that exposes one RTSP path per
LDPlayer instance. mediamtx auto-converts RTSP → WebRTC/WHEP so the iPhone
can connect directly to http://tailscale-ip:8889/instanceN.
"""

import os
import subprocess
import sys
import tempfile
import threading
import traceback
import urllib.error
import urllib.request

from config import ASSETS_DIR, MEDIAMTX_PORT, WHEP_PORT, RTMP_PORT, WEBRTC_UDP_PORT

_API_BASE = "http://127.0.0.1:9997"


def _log(msg: str):
    for _p in [r"C:\ProgramData\WindowControl", r"C:\Windows\Temp"]:
        try:
            os.makedirs(_p, exist_ok=True)
            with open(os.path.join(_p, "service_crash.log"), "a") as f:
                f.write(msg + "\n")
            return
        except Exception:
            continue


def _no_window_flags():
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}
    return {}


def _reap_orphan_mediamtx():
    """Kill leftover mediamtx processes from a crashed/force-closed prior run.

    _stop_locked only kills the process this object spawned. An orphan from a
    previous GUI session still holds the WebRTC UDP mux port (:8000), so the new
    mediamtx logs 'listen udp :8000: bind: Only one usage of each socket
    address' and WebRTC never comes up. Reap by image name before starting.
    """
    if sys.platform != "win32":
        return
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "mediamtx.exe"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
            **_no_window_flags(),
        )
    except Exception:
        _log(f"[mediamtx] reap orphan failed: {traceback.format_exc()[:200]}")


def _mediamtx_exe() -> str:
    bundled = os.path.join(ASSETS_DIR, "mediamtx", "mediamtx.exe")
    if os.path.exists(bundled):
        return bundled
    import shutil
    found = shutil.which("mediamtx")
    if found:
        return found
    return bundled  # will fail at Popen time with a clear error


def _generate_config(instance_names: list[str], tailscale_ip: str | None = None) -> str:
    """Generate mediamtx.yml content for the given instance path names.

    Each LDPlayer instance gets its own always-live mediamtx path
    (instance0, instance1, …) that its scrcpy publishes to and that the browser
    WHEPs directly. There is no shared 'active' mux path: switching instances is
    a fresh WHEP negotiation to the target instance's path, which is simpler and
    avoids the reader-teardown a runtime source-repoint caused.
    """
    # Advertise Tailscale IP as the ICE host so the browser connects directly
    # instead of waiting 20-30s for UDP probes to time out.
    if tailscale_ip:
        nat_lines = (
            f"webrtcAdditionalHosts: [{tailscale_ip}]\n"
            f"webrtcLocalTCPAddress: :{8189}"
        )
    else:
        nat_lines = ""
    paths_config = "\n".join(
        f"  {name}:" for name in instance_names
    )
    return f"""\
logLevel: info
logDestinations: [stdout]

rtspAddress: :{MEDIAMTX_PORT}
rtmpAddress: :{RTMP_PORT}
hlsAddress: :8890
webrtcAddress: :{WHEP_PORT}
webrtcLocalUDPAddress: :{WEBRTC_UDP_PORT}
api: yes
apiAddress: 127.0.0.1:9997
# Real connections establish in ~1-5s (confirmed live). A negotiation
# abandoned before connecting (rapid instance switching, a losing race-probe
# candidate) has no way to signal mediamtx it's been given up on -- WHEP's
# own DELETE was tried but this mediamtx setup doesn't reliably honor it, so
# keeping this timeout itself short is what actually bounds how long an
# abandoned session lingers. Down from the 30s default.
webrtcHandshakeTimeout: 10s
webrtcICEServers2:
  - url: stun:stun.l.google.com:19302
{nat_lines}

paths:
{paths_config}
"""


class MediamtxManager:
    """Manages one mediamtx.exe subprocess for the lifetime of the app."""

    def __init__(self):
        self._proc: subprocess.Popen | None = None
        self._config_file: str | None = None
        self._lock = threading.Lock()
        # Last start() args, so the watchdog can relaunch with the same paths if
        # mediamtx.exe dies on its own.
        self._last_args: tuple | None = None
        self._live_paths: set[str] = set()
        self._stopping = False  # set during an intentional stop() so the watchdog
                                # doesn't fight it
        self._watchdog_thread = threading.Thread(target=self._watchdog, daemon=True)
        self._watchdog_thread.start()

    def start(self, instance_names: list[str], tailscale_ip: str | None = None):
        """Start (or restart) mediamtx with one path per instance."""
        with self._lock:
            self._last_args = (list(instance_names), tailscale_ip)
            self._live_paths = set(instance_names)
            self._stopping = False
            self._stop_locked()
            _reap_orphan_mediamtx()
            cfg = _generate_config(instance_names, tailscale_ip)
            fd, path = tempfile.mkstemp(suffix=".yml", prefix="mediamtx_")
            try:
                os.write(fd, cfg.encode())
                os.close(fd)
            except Exception:
                try:
                    os.close(fd)
                except Exception:
                    pass
                try:
                    os.unlink(path)
                except Exception:
                    pass
                _log(f"[mediamtx] config write failed: {traceback.format_exc()[:300]}")
                return
            self._config_file = path
            exe = _mediamtx_exe()
            try:
                self._proc = subprocess.Popen(
                    [exe, path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    **_no_window_flags(),
                )
                _log(f"[mediamtx] started pid={self._proc.pid} paths={instance_names}")
                threading.Thread(target=self._log_output, daemon=True).start()
            except Exception:
                _log(f"[mediamtx] start failed: {traceback.format_exc()[:400]}")
                self._proc = None
                try:
                    os.unlink(path)
                except Exception:
                    pass
                self._config_file = None

    def _log_output(self):
        proc = self._proc
        if not proc or not proc.stdout:
            return
        try:
            for line in proc.stdout:
                text = line.decode("utf-8", errors="replace").rstrip()
                _log(f"[mediamtx] {text}")
        except Exception:
            pass

    def stop(self):
        with self._lock:
            self._stopping = True
            self._stop_locked()

    def add_path(self, name: str) -> bool:
        """Add a live path to the running instance without restarting it.

        A full restart tears down every other instance's live WHEP stream, so
        this patches the running mediamtx via its config API instead. Falls
        back to a full restart if the API call fails, so this is never worse
        than the old always-restart behavior.
        """
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                return False
            if name in self._live_paths:
                return True
            tailscale_ip = self._last_args[1] if self._last_args else None
        if self._api_call("POST", f"/v3/config/paths/add/{name}", b"{}"):
            with self._lock:
                self._live_paths.add(name)
                self._last_args = (list(self._live_paths), tailscale_ip)
            return True
        _log(f"[mediamtx] add_path {name} via API failed, falling back to full restart")
        with self._lock:
            names = list(self._live_paths) + [name]
        self.start(names, tailscale_ip)
        return True

    def remove_path(self, name: str) -> bool:
        """Remove a live path from the running instance without restarting it."""
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                return False
            if name not in self._live_paths:
                return True
            tailscale_ip = self._last_args[1] if self._last_args else None
        ok = self._api_call("DELETE", f"/v3/config/paths/delete/{name}", None)
        with self._lock:
            self._live_paths.discard(name)
            self._last_args = (list(self._live_paths), tailscale_ip)
        if not ok:
            _log(f"[mediamtx] remove_path {name} via API failed (path left stale until next restart)")
        return ok

    def _api_call(self, method: str, path: str, body: bytes | None) -> bool:
        req = urllib.request.Request(
            f"{_API_BASE}{path}",
            data=body,
            method=method,
            headers={"Content-Type": "application/json"} if body is not None else {},
        )
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                return 200 <= resp.status < 300
        except Exception:
            _log(f"[mediamtx] api {method} {path} failed: {traceback.format_exc()[:200]}")
            return False

    def _watchdog(self):
        """Relaunch mediamtx.exe if it dies on its own.

        The scrcpy sessions and the HTTP server each have their own watchdog;
        mediamtx did not. If it crashed, every stream went black with no
        recovery. This polls every 5s and restarts with the last start() args
        (unless an intentional stop() is in progress).
        """
        import time
        while True:
            time.sleep(5)
            try:
                with self._lock:
                    if self._stopping or self._last_args is None:
                        continue
                    dead = self._proc is not None and self._proc.poll() is not None
                    never_started = self._proc is None
                    args = self._last_args
                if dead or never_started:
                    if dead:
                        _log("[mediamtx] watchdog: process dead — restarting")
                    self.start(args[0], args[1])
            except Exception:
                _log(f"[mediamtx] watchdog error: {traceback.format_exc()[:200]}")

    def _stop_locked(self):
        if self._proc:
            try:
                self._proc.kill()
                self._proc.wait(timeout=3)
            except Exception:
                pass
            self._proc = None
            _log("[mediamtx] stopped")
        if self._config_file and os.path.exists(self._config_file):
            try:
                os.unlink(self._config_file)
            except Exception:
                pass
            self._config_file = None

    @property
    def running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def whep_url(self, instance_name: str, host: str) -> str:
        return f"http://{host}:{WHEP_PORT}/{instance_name}/whep"

    def rtsp_url(self, instance_name: str) -> str:
        return f"rtsp://localhost:{MEDIAMTX_PORT}/{instance_name}"
