"""
ScrcpySession: captures H.264 from LDPlayer via scrcpy-server TCP protocol,
and sends touch/key input via the scrcpy control socket.

Setup (per instance, done by _start_server()):
  adb push scrcpy-server /data/local/tmp/scrcpy-server.jar
  adb shell CLASSPATH=... app_process / com.genymobile.scrcpy.Server 3.1 \
      tunnel_forward=true video_codec=h264 max_fps=30 bit_rate=4000000 \
      send_device_meta=true send_frame_meta=true control=true audio=false &
  adb forward tcp:<port> localabstract:scrcpy_<scid>   (ONE forward — video+control share it)

Protocol (scrcpy-server 3.x, tunnel_forward=true, control=true, audio=false):
  Connect order: video socket first, then control socket (server expects this order).
  Video socket:
    1. Read 1-byte dummy
    2. Read 64-byte device name (zero-padded UTF-8)
    3. Read 4-byte codec_id (big-endian uint32)
    4. Read 8-byte video size: 4+4 width/height
    5. Frame loop: 12-byte header (8 pts_flags + 4 size) + payload
  Control socket (second TCP connection to same forwarded port):
    No handshake — send control messages directly.

Control message format (scrcpy 3.x):
  INJECT_TOUCH_EVENT (type=0x02), 32 bytes total:
    [0]     u8  type=0x02
    [1]     u8  action (0=down, 1=up, 2=move)
    [2-9]   i64 pointerId (big-endian, use 0 for single touch)
    [10-13] i32 x (big-endian, pixel coords)
    [14-17] i32 y (big-endian, pixel coords)
    [18-19] u16 screenWidth  (big-endian)
    [20-21] u16 screenHeight (big-endian)
    [22-23] u16 pressure (big-endian, 0xffff=1.0 pressed, 0=released)
    [24-27] u32 actionButton (big-endian, 0 for touch)
    [28-31] u32 buttons (big-endian, 0 for touch)  ← total 32 bytes per scrcpy 3.x
  INJECT_KEYCODE (type=0x00), 14 bytes total:
    [0]     u8  type=0x00
    [1]     u8  action (0=down, 1=up)
    [2-5]   i32 keycode (big-endian, Android KeyEvent keycode)
    [6-9]   i32 repeat (big-endian, 0)
    [10-13] i32 metaState (big-endian, 0)

Pipeline per instance:
  Video:   scrcpy-server → TCP → Python → ffmpeg stdin → RTSP → mediamtx → WHEP
  Control: WebSocket handler → ScrcpyControl.send_touch() → TCP → scrcpy-server → Android
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


def _start_server(adb: str, serial: str, port: int, scid: int) -> bool:
    """Push server jar, launch it, set up ONE adb forward.

    With tunnel_forward=true, scrcpy-server opens a single LocalServerSocket and
    accepts connections in order: video first, then control. Both go to the same
    abstract socket — one adb forward covers both.
    """
    nw = _no_window_flags()
    jar = _server_jar_path()
    if not os.path.exists(jar):
        _log(f"[scrcpy] server jar not found: {jar}")
        return False

    socket_name = f"scrcpy_{scid:08x}"

    try:
        subprocess.run(
            [adb, "-s", serial, "push", jar, "/data/local/tmp/scrcpy-server.jar"],
            capture_output=True, timeout=15, **nw,
        )
        subprocess.run(
            [adb, "-s", serial, "shell", f"pkill -f 'scrcpy-server.*scid={scid:x}'"],
            capture_output=True, timeout=5, **nw,
        )
        time.sleep(0.3)
        subprocess.Popen(
            [
                adb, "-s", serial, "shell",
                "CLASSPATH=/data/local/tmp/scrcpy-server.jar"
                f" app_process / com.genymobile.scrcpy.Server 3.1"
                f" tunnel_forward=true video_codec=h264"
                f" max_fps=30 bit_rate=4000000"
                f" send_device_meta=true send_frame_meta=true"
                f" control=true audio=false"
                f" video_encoder_options=i-frame-interval=2"
                f" scid={scid:x}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **nw,
        )
        time.sleep(0.5)
        result = subprocess.run(
            [adb, "-s", serial, "forward", f"tcp:{port}", f"localabstract:{socket_name}"],
            capture_output=True, timeout=5, **nw,
        )
        if result.returncode != 0:
            _log(f"[scrcpy] forward failed serial={serial}: {result.stderr.decode()[:200]}")
            return False
        _log(f"[scrcpy] server ready serial={serial} scid={scid} port={port} socket={socket_name}")
        return True
    except Exception:
        _log(f"[scrcpy] _start_server error serial={serial}: {traceback.format_exc()[:400]}")
        return False


class ScrcpyControl:
    """Sends touch and key events to scrcpy-server via the control socket.

    The control socket is a persistent TCP connection to the same forwarded port
    as the video socket. scrcpy-server accepts connections in order: video first,
    then control. ScrcpySession connects video first, waits for the header, then
    ScrcpyControl connects second.

    Thread-safe: send() acquires a lock before writing.
    """

    # Action constants (Android MotionEvent)
    ACTION_DOWN = 0
    ACTION_UP   = 1
    ACTION_MOVE = 2

    # Android keycodes used for back/home/menu
    KEYCODE_BACK   = 4
    KEYCODE_HOME   = 3
    KEYCODE_MENU   = 82
    KEYCODE_VOLUME_UP   = 24
    KEYCODE_VOLUME_DOWN = 25

    def __init__(self, control_port: int, serial: str):
        self._port = control_port
        self._serial = serial
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()

    def connect(self) -> bool:
        """Open control socket. Call after video socket handshake completes."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect(("127.0.0.1", self._port))
            sock.settimeout(None)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self._lock:
                self._sock = sock
            _log(f"[control] connected serial={self._serial} port={self._port}")
            return True
        except Exception:
            _log(f"[control] connect failed serial={self._serial}: {traceback.format_exc()[:200]}")
            return False

    def send_touch(self, action: int, nx: float, ny: float, w: int, h: int,
                   pointer_id: int = 0):
        """Send INJECT_TOUCH_EVENT. nx/ny are normalized [0,1] coords."""
        x = int(nx * w)
        y = int(ny * h)
        pressure = 0xffff if action != self.ACTION_UP else 0
        # 32-byte INJECT_TOUCH_EVENT (scrcpy 3.x)
        msg = struct.pack(">BBQiiHHHII",
            0x02,           # type: INJECT_TOUCH_EVENT
            action,         # action (u8)
            pointer_id,     # pointerId (u64)
            x, y,           # x, y (i32 each)
            w & 0xffff,     # screenWidth (u16)
            h & 0xffff,     # screenHeight (u16)
            pressure,       # pressure (u16, 0xffff = 1.0)
            0,              # actionButton (u32)
            0,              # buttons (u32)
        )
        self._send(msg)

    def send_keycode(self, keycode: int):
        """Send INJECT_KEYCODE down+up for a single Android keycode."""
        for action in (0, 1):  # down, up
            msg = struct.pack(">BBiii",
                0x00,       # type: INJECT_KEYCODE
                action,     # action
                keycode,    # keycode
                0,          # repeat
                0,          # metaState
            )
            self._send(msg)

    def _send(self, data: bytes):
        with self._lock:
            if self._sock is None:
                return
            try:
                self._sock.sendall(data)
            except Exception:
                _log(f"[control] send error serial={self._serial}, reconnecting")
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None

    def close(self):
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._sock is not None


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
        # Control socket connects to same port as video — server accepts both sequentially
        self.control = ScrcpyControl(self._tcp_port, serial)

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

        if not _start_server(adb, self.serial, self._tcp_port, self.instance_index):
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
            # scrcpy-server (tunnel_forward=true, control=true) accept order:
            #   1. accept video  → sends dummy byte immediately
            #   2. accept control (blocks here)
            #   3. sends device_meta + codec header on video socket
            # So: connect video, read dummy byte, connect control, then read the rest.
            video_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            video_sock.settimeout(10)
            video_sock.connect(("127.0.0.1", self._tcp_port))

            _recvall(video_sock, 1)   # dummy byte — sent right after video accept

            # Server now blocks on accept() for control socket.
            # Connect control to unblock it so it proceeds to send device_meta.
            self.control.connect()

            device_name = _recvall(video_sock, 64).rstrip(b"\x00").decode("utf-8", errors="replace")
            codec_id = struct.unpack(">I", _recvall(video_sock, 4))[0]
            init_w = struct.unpack(">I", _recvall(video_sock, 4))[0]
            init_h = struct.unpack(">I", _recvall(video_sock, 4))[0]
            _log(f"[scrcpy] handshake device={device_name!r} codec=0x{codec_id:08x} {init_w}x{init_h}")

            video_sock.settimeout(None)

            ffmpeg_proc = subprocess.Popen(
                [
                    ffmpeg_exe,
                    "-loglevel", "warning",
                    "-use_wallclock_as_timestamps", "1",
                    "-f", "h264",
                    "-i", "pipe:0",
                    "-c:v", "libx264",
                    "-preset", "ultrafast",
                    "-tune", "zerolatency",
                    "-g", "60",
                    "-sc_threshold", "0",
                    "-b:v", "4M",
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
        self.control.close()
        _log(f"[scrcpy] stopped serial={self.serial}")

    @property
    def alive(self) -> bool:
        with self._lock:
            return self._running and self._ffmpeg_proc is not None
