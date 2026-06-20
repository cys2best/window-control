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

from config import ASSETS_DIR, MEDIAMTX_PORT, WHEP_PORT, RTMP_PORT


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
    """Generate mediamtx.yml content for the given instance path names."""
    paths = "\n".join(f"  {name}:" for name in instance_names)
    # Advertise Tailscale IP as the ICE host so the browser connects directly
    # instead of waiting 20-30s for UDP probes to time out.
    stun_lines = "webrtcICEServers2:\n- url: stun:stun.l.google.com:19302"
    if tailscale_ip:
        nat_lines = (
            f"webrtcICEHostNAT1To1IPs: [{tailscale_ip}]\n"
            f"webrtcICETCPMuxAddress: 0.0.0.0:{8189}\n"
            f"{stun_lines}"
        )
    else:
        nat_lines = stun_lines
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
api: no
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

    def start(self, instance_names: list[str], tailscale_ip: str | None = None):
        """Start (or restart) mediamtx with paths for the given instances."""
        with self._lock:
            self._stop_locked()
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
                _log(f"[mediamtx] {line.decode('utf-8', errors='replace').rstrip()}")
        except Exception:
            pass

    def stop(self):
        with self._lock:
            self._stop_locked()

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
