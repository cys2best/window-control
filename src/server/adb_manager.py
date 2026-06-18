"""
ADB-based Android VM capture and input for LDPlayer/VirtualBox headless instances.

Capture pipeline:
  adb exec-out screenrecord --output-format=h264 --time-limit=3600 -
    | ffmpeg -i pipe:0 -vf fps=15 -f image2pipe -vcodec mjpeg pipe:1
    -> Python reads JPEG frames -> FrameQueue

Input:
  adb shell input tap X Y
  adb shell input keyevent KEYCODE
"""

import os
import re
import subprocess
import sys
import threading
import traceback


_ADB_PATH_FALLBACKS = [
    r"C:\LDPlayer\LDPlayer9\adb.exe",
    r"C:\LDPlayer\LDPlayer4.0\adb.exe",
    r"C:\LDPlayer\LDPlayer4.0\vbox64\adb.exe",
    r"C:\LDPlayer\OSLink\bin\adb.exe",
    r"C:\Program Files\LDPlayer\LDPlayer9\adb.exe",
    r"C:\Program Files\LDPlayer\LDPlayer4.0\adb.exe",
    r"C:\LDPlayer9\adb.exe",
    r"C:\LDPlayer4\adb.exe",
]


def _log(msg: str):
    for _p in [r"C:\ProgramData\WindowControl", r"C:\Windows\Temp"]:
        try:
            os.makedirs(_p, exist_ok=True)
            with open(os.path.join(_p, "service_crash.log"), "a") as f:
                f.write(msg + "\n")
            return
        except Exception:
            continue


def _find_adb() -> str | None:
    for path in _ADB_PATH_FALLBACKS:
        if os.path.exists(path):
            _log(f"[adb] found at {path}")
            return path
    # Try adb.exe on PATH
    import shutil
    found = shutil.which("adb")
    if found:
        _log(f"[adb] found on PATH: {found}")
        return found
    _log(f"[adb] not found — tried: {_ADB_PATH_FALLBACKS}")
    return None


def _get_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _find_ldplayer_window(index: int) -> int | None:
    """Return HWND of LDPlayer window for given instance index, or None."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        user32 = ctypes.windll.user32
        # LDPlayer window titles: "LDPlayer", "LDPlayer-1", "LDPlayer 1", etc.
        candidates = [
            f"LDPlayer",
            f"LDPlayer-{index}",
            f"LDPlayer {index}",
            f"LDPlayer#{index}",
            f"LDPlayer4",
            f"LDPlayer4-{index}",
            f"LDPlayer9",
            f"LDPlayer9-{index}",
        ]
        for title in candidates:
            hwnd = user32.FindWindowW(None, title)
            if hwnd:
                return hwnd
        # Fallback: enumerate all windows, find one containing "LDPlayer"
        found = []
        EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
        buf = ctypes.create_unicode_buffer(256)
        def _cb(hwnd, _):
            user32.GetWindowTextW(hwnd, buf, 256)
            t = buf.value
            if "LDPlayer" in t and user32.IsWindowVisible(hwnd):
                found.append((hwnd, t))
            return True
        user32.EnumWindows(EnumWindowsProc(_cb), 0)
        if found:
            # Pick by index if multiple
            if index < len(found):
                return found[index][0]
            return found[0][0]
    except Exception:
        pass
    return None


def maximize_ldplayer_window(index: int):
    """Bring LDPlayer instance window to foreground and maximize it."""
    if sys.platform != "win32":
        return
    hwnd = _find_ldplayer_window(index)
    if not hwnd:
        _log(f"[ldplayer] window not found for index={index}")
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        SW_MAXIMIZE = 3
        user32.ShowWindow(hwnd, SW_MAXIMIZE)
        user32.SetForegroundWindow(hwnd)
        _log(f"[ldplayer] maximized hwnd={hwnd} index={index}")
    except Exception:
        _log(f"[ldplayer] maximize failed: {traceback.format_exc()[:200]}")


def list_vms() -> list[dict]:
    """Return list of connected ADB devices as VM dicts with id='adb:SERIAL'."""
    adb = _find_adb()
    if not adb:
        _log("[adb] adb.exe not found")
        return []
    try:
        out = subprocess.check_output([adb, "devices"], timeout=5, text=True,
                                      **_no_window_flags())
        _log(f"[adb] devices output: {out.strip()!r}")
        result = []
        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line or "\t" not in line:
                continue
            serial, state = line.split("\t", 1)
            if state.strip() != "device":
                continue
            m = re.match(r"emulator-(\d+)", serial)
            if m:
                port = int(m.group(1))
                idx = (port - 5554) // 2
                name = f"LDPlayer #{idx}"
            else:
                idx = 0
                name = serial
            result.append({
                "id": f"adb:{serial}",
                "title": f"[VM] {name}",
                "ldplayer_index": idx,
            })
        return result
    except Exception:
        _log(f"[adb] list_vms failed: {traceback.format_exc()[:300]}")
        return []


def get_screen_size(serial: str) -> tuple[int, int]:
    adb = _find_adb()
    if not adb:
        return 1280, 720
    try:
        out = subprocess.check_output(
            [adb, "-s", serial, "shell", "wm size"], timeout=5, text=True,
            **_no_window_flags()
        )
        m = re.search(r"(\d+)x(\d+)", out)
        if m:
            return int(m.group(1)), int(m.group(2))
    except Exception:
        pass
    return 1280, 720


class AdbSession:
    """Holds the screenrecord+ffmpeg pipeline for one ADB device."""

    def __init__(self, serial: str, w: int, h: int, fps: int = 15, ldplayer_index: int = 0):
        self.serial = serial
        self.w = w
        self.h = h
        self.fps = fps
        self.ldplayer_index = ldplayer_index
        self._record_proc: subprocess.Popen | None = None
        self._ffmpeg_proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._latest_frame: bytes | None = None
        self._reader_thread: threading.Thread | None = None
        self._running = False

    def start(self) -> bool:
        adb = _find_adb()
        ffmpeg = _get_ffmpeg()
        if not adb:
            _log("[adb] adb.exe not found")
            return False
        if not ffmpeg:
            _log("[adb] ffmpeg not found — install imageio-ffmpeg")
            return False
        try:
            nw = _no_window_flags()
            self._record_proc = subprocess.Popen(
                [adb, "-s", self.serial, "exec-out",
                 f"screenrecord --output-format=h264 --bit-rate=2000000 --size={self.w}x{self.h} -"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                **nw,
            )
            self._ffmpeg_proc = subprocess.Popen(
                [ffmpeg, "-loglevel", "quiet",
                 "-i", "pipe:0",
                 "-vf", f"fps={self.fps}",
                 "-f", "image2pipe",
                 "-vcodec", "mjpeg",
                 "-q:v", "5",
                 "pipe:1"],
                stdin=self._record_proc.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                **nw,
            )
            self._record_proc.stdout.close()
            self._running = True
            self._reader_thread = threading.Thread(target=self._read_frames, daemon=True)
            self._reader_thread.start()
            _log(f"[adb] session started serial={self.serial} {self.w}x{self.h}@{self.fps}fps")
            return True
        except Exception:
            _log(f"[adb] session start failed: {traceback.format_exc()[:400]}")
            self.stop()
            return False

    def _read_frames(self):
        """Parse JPEG frames from ffmpeg stdout (SOI=FFD8, EOI=FFD9)."""
        buf = b""
        ffmpeg = self._ffmpeg_proc
        if ffmpeg is None:
            return
        try:
            while self._running:
                chunk = ffmpeg.stdout.read(65536)
                if not chunk:
                    break
                buf += chunk
                while True:
                    start = buf.find(b"\xff\xd8")
                    if start == -1:
                        buf = b""
                        break
                    end = buf.find(b"\xff\xd9", start + 2)
                    if end == -1:
                        buf = buf[start:]
                        break
                    jpeg = buf[start:end + 2]
                    buf = buf[end + 2:]
                    with self._lock:
                        self._latest_frame = jpeg
        except Exception:
            pass
        _log(f"[adb] frame reader exited serial={self.serial}")

    def get_latest_frame(self) -> bytes | None:
        with self._lock:
            return self._latest_frame

    def stop(self):
        self._running = False
        for proc in [self._ffmpeg_proc, self._record_proc]:
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._ffmpeg_proc = None
        self._record_proc = None
        _log(f"[adb] session stopped serial={self.serial}")


# ── Input ─────────────────────────────────────────────────────────────────────

def _no_window_flags():
    """Return CREATE_NO_WINDOW flag on Windows to suppress cmd flashes."""
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


_KEYCODES = {
    "Return":    "66",
    "BackSpace": "67",
    "Tab":       "61",
    "Escape":    "111",
    "Delete":    "112",
    "ArrowLeft": "21",
    "ArrowUp":   "19",
    "ArrowRight":"22",
    "ArrowDown": "20",
    " ":         "62",
    "Space":     "62",
}


class InputSession:
    """Persistent `adb shell` process — sends input commands over stdin.

    One per ADB serial. Eliminates per-tap process spawn overhead (~200-400ms).
    Commands are newline-terminated shell one-liners sent to `adb shell` stdin.
    """

    def __init__(self, serial: str):
        self.serial = serial
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()

    def _ensure(self) -> bool:
        if self._proc and self._proc.poll() is None:
            return True
        adb = _find_adb()
        if not adb:
            return False
        try:
            self._proc = subprocess.Popen(
                [adb, "-s", self.serial, "shell"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                **_no_window_flags(),
            )
            return True
        except Exception:
            return False

    def send(self, cmd: str):
        with self._lock:
            if not self._ensure():
                return
            try:
                self._proc.stdin.write((cmd + "\n").encode())
                self._proc.stdin.flush()
            except Exception:
                self._proc = None

    def stop(self):
        with self._lock:
            if self._proc:
                try:
                    self._proc.stdin.close()
                    self._proc.kill()
                except Exception:
                    pass
                self._proc = None


# Per-serial InputSession cache — created on first use, reused thereafter
_input_sessions: dict[str, InputSession] = {}
_input_sessions_lock = threading.Lock()


def _get_input_session(serial: str) -> InputSession:
    with _input_sessions_lock:
        if serial not in _input_sessions:
            _input_sessions[serial] = InputSession(serial)
        return _input_sessions[serial]


def tap(serial: str, nx: float, ny: float, w: int, h: int):
    x, y = int(nx * w), int(ny * h)
    _get_input_session(serial).send(f"input tap {x} {y}")


def swipe(serial: str, nx0: float, ny0: float, nx1: float, ny1: float,
          w: int, h: int, duration_ms: int = 50):
    x0, y0 = int(nx0 * w), int(ny0 * h)
    x1, y1 = int(nx1 * w), int(ny1 * h)
    _get_input_session(serial).send(
        f"input swipe {x0} {y0} {x1} {y1} {duration_ms}"
    )


def scroll(serial: str, nx: float, ny: float, dy: int, w: int, h: int):
    x, y = int(nx * w), int(ny * h)
    dist = dy * 200
    _get_input_session(serial).send(
        f"input swipe {x} {y} {x} {y - dist} 300"
    )


def send_key(serial: str, key: str):
    kc = _KEYCODES.get(key)
    if kc:
        _get_input_session(serial).send(f"input keyevent {kc}")
    elif len(key) == 1:
        escaped = key.replace("\\", "\\\\").replace("'", "\\'") \
                     .replace('"', '\\"').replace(" ", "%s") \
                     .replace("&", "\\&").replace("<", "\\<") \
                     .replace(">", "\\>").replace("|", "\\|")
        _get_input_session(serial).send(f"input text {escaped}")
