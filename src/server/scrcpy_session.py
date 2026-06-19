"""
ScrcpySession: captures H.264 from a LDPlayer instance via scrcpy-server TCP
protocol, strips the protocol framing, and pipes raw H.264 to ffmpeg which
pushes an RTSP stream to mediamtx.

scrcpy-server TCP protocol (v2):
  Connection:
    1. adb forward tcp:<local_port> localabstract:scrcpy
    2. Connect to 127.0.0.1:<local_port>  (two sockets: video + control)
    3. Read 1-byte version  (video socket)
    4. Read 64-byte device name  (video socket, zero-padded)
    5. Read 4-byte codec id (0x68323634 = 'h264')
    6. Read 4-byte initial width, 4-byte initial height
  Frames (video socket, repeated):
    7. Read 8-byte PTS (big-endian uint64, microseconds; 0xFFFFFFFFFFFFFFFF = config packet)
    8. Read 4-byte payload size (big-endian uint32)
    9. Read <size> bytes — raw H.264 Annex B NAL units

scrcpy.exe handles pushing scrcpy-server.jar to the device automatically.
"""

import os
import socket
import struct
import subprocess
import sys
import threading
import time
import traceback

from config import ASSETS_DIR


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


def _scrcpy_exe() -> str:
    bundled = os.path.join(ASSETS_DIR, "scrcpy", "scrcpy.exe")
    if os.path.exists(bundled):
        return bundled
    import shutil
    found = shutil.which("scrcpy")
    if found:
        return found
    return bundled


def _get_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


# Base port for scrcpy TCP forwarding. Instance 0 → 27183, instance 1 → 27184, etc.
_SCRCPY_BASE_PORT = 27183


class ScrcpySession:
    """Manages one scrcpy capture + ffmpeg RTSP push for one LDPlayer instance."""

    def __init__(self, serial: str, instance_index: int, rtsp_url: str,
                 w: int, h: int):
        self.serial = serial
        self.instance_index = instance_index
        self.rtsp_url = rtsp_url
        self.w = w
        self.h = h
        self._tcp_port = _SCRCPY_BASE_PORT + instance_index
        self._scrcpy_proc: subprocess.Popen | None = None
        self._ffmpeg_proc: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()

    def start(self) -> bool:
        with self._lock:
            self._stop_locked()
            self._running = True
            try:
                exe = _scrcpy_exe()
                self._scrcpy_proc = subprocess.Popen(
                    [
                        exe,
                        "--serial", self.serial,
                        "--no-display",
                        "--video-codec", "h264",
                        "--video-bit-rate", "4M",
                        "--max-fps", "30",
                        "--port", str(self._tcp_port),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    **_no_window_flags(),
                )
                _log(f"[scrcpy] started serial={self.serial} port={self._tcp_port}")
            except Exception:
                _log(f"[scrcpy] start failed serial={self.serial}: {traceback.format_exc()[:400]}")
                self._stop_locked()
                return False

        # Sleep outside lock — waits for scrcpy-server to bind the TCP port
        time.sleep(1.5)

        self._reader_thread = threading.Thread(
            target=self._stream_loop, daemon=True
        )
        self._reader_thread.start()
        return True

    def _stream_loop(self):
        """Connect to scrcpy-server TCP, parse protocol, pipe H.264 to ffmpeg→RTSP."""
        ffmpeg_exe = _get_ffmpeg()
        if not ffmpeg_exe:
            _log("[scrcpy] ffmpeg not found")
            return

        ffmpeg_proc: subprocess.Popen | None = None
        sock: socket.socket | None = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)
            sock.connect(("127.0.0.1", self._tcp_port))
            _log(f"[scrcpy] connected to TCP port {self._tcp_port}")

            # Read protocol header
            version = sock.recv(1)
            device_name = _recvall(sock, 64).rstrip(b"\x00").decode("utf-8", errors="replace")
            codec_id = struct.unpack(">I", _recvall(sock, 4))[0]
            init_w = struct.unpack(">I", _recvall(sock, 4))[0]
            init_h = struct.unpack(">I", _recvall(sock, 4))[0]
            _log(f"[scrcpy] handshake ver={version} device={device_name!r} "
                 f"codec=0x{codec_id:08x} {init_w}x{init_h}")

            sock.settimeout(None)

            # Launch ffmpeg reading from stdin, pushing RTSP to mediamtx
            ffmpeg_proc = subprocess.Popen(
                [
                    ffmpeg_exe,
                    "-loglevel", "quiet",
                    "-fflags", "nobuffer",
                    "-flags", "low_delay",
                    "-probesize", "32",
                    "-analyzeduration", "0",
                    "-f", "h264",
                    "-i", "pipe:0",
                    "-c:v", "copy",
                    "-f", "rtsp",
                    "-rtsp_transport", "tcp",
                    self.rtsp_url,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **_no_window_flags(),
            )
            with self._lock:
                self._ffmpeg_proc = ffmpeg_proc

            _log(f"[scrcpy] ffmpeg started → {self.rtsp_url}")

            # Read frames and pipe H.264 payload to ffmpeg stdin
            _CONFIG_PTS = 0xFFFFFFFFFFFFFFFF
            while self._running:
                header = _recvall(sock, 12)
                if len(header) < 12:
                    break
                pts = struct.unpack(">Q", header[:8])[0]
                size = struct.unpack(">I", header[8:12])[0]
                payload = _recvall(sock, size)
                if len(payload) < size:
                    break
                # Config (SPS/PPS) packets are still valid H.264 — pass through
                _ = pts == _CONFIG_PTS
                try:
                    ffmpeg_proc.stdin.write(payload)
                    ffmpeg_proc.stdin.flush()
                except Exception:
                    break

        except Exception:
            _log(f"[scrcpy] stream_loop error: {traceback.format_exc()[:400]}")
        finally:
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
            # Close ffmpeg cleanly — use the local reference, which is always valid here
            if ffmpeg_proc:
                try:
                    ffmpeg_proc.stdin.close()
                except Exception:
                    pass
                try:
                    ffmpeg_proc.kill()
                except Exception:
                    pass
            # Clear the shared reference if it still points to this process
            with self._lock:
                if self._ffmpeg_proc is ffmpeg_proc:
                    self._ffmpeg_proc = None
            _log(f"[scrcpy] stream_loop exited serial={self.serial}")

    def stop(self):
        with self._lock:
            self._stop_locked()

    def _stop_locked(self):
        """Must be called with self._lock held."""
        self._running = False
        for proc in [self._ffmpeg_proc, self._scrcpy_proc]:
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._ffmpeg_proc = None
        self._scrcpy_proc = None
        _log(f"[scrcpy] stopped serial={self.serial}")

    @property
    def alive(self) -> bool:
        with self._lock:
            return (
                self._running
                and self._scrcpy_proc is not None
                and self._scrcpy_proc.poll() is None
            )


def _recvall(sock: socket.socket, n: int) -> bytes:
    """Read exactly n bytes from socket."""
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf
