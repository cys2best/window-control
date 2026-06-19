"""
ScrcpySession: captures H.264 from LDPlayer via scrcpy-server TCP protocol.

Setup (per instance, done by _start_server()):
  adb push scrcpy-server /data/local/tmp/scrcpy-server.jar
  adb shell CLASSPATH=... app_process / com.genymobile.scrcpy.Server 3.1 \
      tunnel_forward=true video_codec=h264 max_fps=30 bit_rate=4000000 \
      send_device_meta=true send_frame_meta=true control=false audio=false &
  adb forward tcp:<port> localabstract:scrcpy

Protocol (scrcpy-server 3.x, tunnel_forward=true, video only, no audio/control):
  1. Open ONE TCP socket to 127.0.0.1:<port>  (video socket)
  2. Read 1-byte dummy (connection probe byte)
  3. Read 64-byte device name (zero-padded UTF-8, only if send_device_meta=true)
  4. Read 4-byte codec_id (big-endian uint32, 0x68323634 = 'h264')
  5. Read 8-byte video size: 4-byte width + 4-byte height (big-endian uint32 each)
  6. Frame loop:
     a. 8-byte pts_flags  (big-endian uint64)
        bit 63 = config packet (SPS/PPS), bit 62 = key frame
        pts = pts_flags & 0x3FFFFFFFFFFFFFFF
     b. 4-byte size       (big-endian uint32)
     c. <size> bytes      raw H.264 Annex B payload
     Config packets pass through to ffmpeg unchanged.

Pipeline per instance:
  scrcpy-server (on device) → TCP → Python → ffmpeg stdin → RTSP → mediamtx → WHEP
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

_SCRCPY_BASE_PORT = 27183   # instance 0 → 27183, instance 1 → 27184, …
_SERVER_JAR = "scrcpy-server"  # filename in assets/scrcpy/

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


def _find_adb() -> str | None:
    from server.adb_manager import _find_adb as _adb
    return _adb()


def _get_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _server_jar_path() -> str:
    return os.path.join(ASSETS_DIR, "scrcpy", _SERVER_JAR)


def _recvall(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


def _start_server(adb: str, serial: str, port: int) -> bool:
    """Push server jar, launch it, set up adb forward. Idempotent."""
    nw = _no_window_flags()
    jar = _server_jar_path()
    if not os.path.exists(jar):
        _log(f"[scrcpy] server jar not found: {jar}")
        return False

    try:
        # Push jar to device
        subprocess.run(
            [adb, "-s", serial, "push", jar, "/data/local/tmp/scrcpy-server.jar"],
            capture_output=True, timeout=15, **nw,
        )
        # Kill any existing server instance
        subprocess.run(
            [adb, "-s", serial, "shell", "pkill", "-f", "scrcpy-server"],
            capture_output=True, timeout=5, **nw,
        )
        time.sleep(0.3)
        # Launch server in background — tunnel_forward so it listens on abstract socket
        subprocess.Popen(
            [
                adb, "-s", serial, "shell",
                "CLASSPATH=/data/local/tmp/scrcpy-server.jar"
                " app_process / com.genymobile.scrcpy.Server 3.1"
                " tunnel_forward=true video_codec=h264"
                " max_fps=30 bit_rate=4000000"
                " send_device_meta=true send_frame_meta=true"
                " control=false audio=false",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **nw,
        )
        time.sleep(0.5)
        # Forward local TCP port to device abstract socket
        result = subprocess.run(
            [adb, "-s", serial, "forward", f"tcp:{port}", "localabstract:scrcpy"],
            capture_output=True, timeout=5, **nw,
        )
        if result.returncode != 0:
            _log(f"[scrcpy] forward failed serial={serial}: {result.stderr.decode()[:200]}")
            return False
        _log(f"[scrcpy] server ready serial={serial} port={port}")
        return True
    except Exception:
        _log(f"[scrcpy] _start_server error serial={serial}: {traceback.format_exc()[:400]}")
        return False


class ScrcpySession:
    """Manages scrcpy-server capture + ffmpeg RTSP push for one LDPlayer instance."""

    def __init__(self, serial: str, instance_index: int, rtsp_url: str,
                 w: int, h: int):
        self.serial = serial
        self.instance_index = instance_index
        self.rtsp_url = rtsp_url
        self.w = w
        self.h = h
        self._tcp_port = _SCRCPY_BASE_PORT + instance_index
        self._ffmpeg_proc: subprocess.Popen | None = None
        self._stream_thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()

    def start(self) -> bool:
        adb = _find_adb()
        if not adb:
            _log(f"[scrcpy] adb not found serial={self.serial}")
            return False
        if not _get_ffmpeg():
            _log(f"[scrcpy] ffmpeg not found serial={self.serial}")
            return False

        with self._lock:
            self._stop_locked()
            self._running = True

        if not _start_server(adb, self.serial, self._tcp_port):
            with self._lock:
                self._running = False
            return False

        # Give server time to start listening
        time.sleep(1.0)

        self._stream_thread = threading.Thread(target=self._stream_loop, daemon=True)
        self._stream_thread.start()
        _log(f"[scrcpy] started serial={self.serial} port={self._tcp_port}")
        return True

    def _stream_loop(self):
        ffmpeg_exe = _get_ffmpeg()
        ffmpeg_proc: subprocess.Popen | None = None
        video_sock: socket.socket | None = None
        try:
            # One connection: video socket (audio=false, control=false)
            video_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            video_sock.settimeout(10)
            video_sock.connect(("127.0.0.1", self._tcp_port))

            # Protocol header
            _recvall(video_sock, 1)   # dummy byte (connection probe)
            device_name = _recvall(video_sock, 64).rstrip(b"\x00").decode("utf-8", errors="replace")
            codec_id = struct.unpack(">I", _recvall(video_sock, 4))[0]
            init_w = struct.unpack(">I", _recvall(video_sock, 4))[0]
            init_h = struct.unpack(">I", _recvall(video_sock, 4))[0]
            _log(f"[scrcpy] handshake device={device_name!r} codec=0x{codec_id:08x} {init_w}x{init_h}")

            video_sock.settimeout(None)

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
                stderr=subprocess.PIPE,
                **_no_window_flags(),
            )
            with self._lock:
                self._ffmpeg_proc = ffmpeg_proc

            _log(f"[scrcpy] streaming serial={self.serial} → {self.rtsp_url}")

            _FLAG_CONFIG = (1 << 63)
            while self._running:
                header = _recvall(video_sock, 12)
                if len(header) < 12:
                    break
                pts_flags = struct.unpack(">Q", header[:8])[0]
                size = struct.unpack(">I", header[8:12])[0]
                payload = _recvall(video_sock, size)
                if len(payload) < size:
                    break
                # Config packets (SPS/PPS) are valid H.264 — pass through
                _ = bool(pts_flags & _FLAG_CONFIG)
                try:
                    ffmpeg_proc.stdin.write(payload)
                    ffmpeg_proc.stdin.flush()
                except Exception:
                    break

        except Exception:
            _log(f"[scrcpy] stream_loop error serial={self.serial}: {traceback.format_exc()[:400]}")
        finally:
            if video_sock:
                try:
                    video_sock.close()
                except Exception:
                    pass
            if ffmpeg_proc:
                try:
                    ffmpeg_proc.stdin.close()
                except Exception:
                    pass
                try:
                    ffmpeg_proc.kill()
                except Exception:
                    pass
                try:
                    stderr_bytes = ffmpeg_proc.stderr.read()
                    if stderr_bytes:
                        _log(f"[scrcpy] ffmpeg stderr serial={self.serial}: "
                             f"{stderr_bytes.decode('utf-8', errors='replace')[:600]}")
                except Exception:
                    pass
            with self._lock:
                if self._ffmpeg_proc is ffmpeg_proc:
                    self._ffmpeg_proc = None
            _log(f"[scrcpy] stream_loop exited serial={self.serial}")
            with self._lock:
                self._running = False

    def stop(self):
        with self._lock:
            self._stop_locked()

    def _stop_locked(self):
        self._running = False
        if self._ffmpeg_proc:
            try:
                self._ffmpeg_proc.kill()
            except Exception:
                pass
            self._ffmpeg_proc = None
        _log(f"[scrcpy] stopped serial={self.serial}")

    @property
    def alive(self) -> bool:
        with self._lock:
            return self._running and self._ffmpeg_proc is not None
