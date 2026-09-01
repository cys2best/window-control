"""ADB discovery, screenshots, and input for LDPlayer instances."""

import os
import re
import subprocess
import sys
import threading
import traceback


def _bundled_adb() -> str:
    from config import ASSETS_DIR
    return os.path.join(ASSETS_DIR, "scrcpy", "adb.exe")


_ADB_PATH_FALLBACKS = [
    _bundled_adb(),                           # shipped by download_assets.py via scrcpy zip
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


_adb_path_cache: str | None = None
_adb_path_searched: bool = False

def _find_adb() -> str | None:
    global _adb_path_cache, _adb_path_searched
    if _adb_path_searched:
        return _adb_path_cache
    _adb_path_searched = True
    for path in _ADB_PATH_FALLBACKS:
        if os.path.exists(path):
            _log(f"[adb] found at {path}")
            _adb_path_cache = path
            return path
    import shutil
    found = shutil.which("adb")
    if found:
        _log(f"[adb] found on PATH: {found}")
        _adb_path_cache = found
        return found
    _log(f"[adb] not found — tried: {_ADB_PATH_FALLBACKS}")
    return None


def _find_ldconsole() -> str | None:
    """Locate the LDPlayer console executable using discovery's existing paths."""
    if sys.platform != "win32":
        return None
    # ldconsole.exe lives at the LDPlayer install ROOT, not next to adb (adb may
    # be the bundled scrcpy adb or resolved via PATH). Search install roots too.
    candidates: list[str] = []
    adb = _find_adb()
    if adb:
        root = os.path.dirname(adb)
        candidates += [
            os.path.join(root, "ldconsole.exe"),
            os.path.join(os.path.dirname(root), "ldconsole.exe"),
            os.path.join(root, "dnconsole.exe"),
            os.path.join(os.path.dirname(root), "dnconsole.exe"),
        ]
    for base in (
        r"C:\LDPlayer\LDPlayer9",
        r"C:\LDPlayer\LDPlayer4.0",
        r"C:\Program Files\LDPlayer\LDPlayer9",
        r"C:\Program Files\LDPlayer\LDPlayer4.0",
        r"C:\LDPlayer9",
        r"C:\LDPlayer4",
    ):
        candidates.append(os.path.join(base, "ldconsole.exe"))
        candidates.append(os.path.join(base, "dnconsole.exe"))
    exe = next((path for path in candidates if os.path.exists(path)), None)
    if not exe:
        _log(f"[ldplayer] ldconsole.exe not found — tried: {candidates}")
    return exe


def _get_ldconsole_names() -> dict[int, str]:
    """Map LDPlayer instance index → title via `ldconsole.exe list2`.

    This is the AUTHORITATIVE index→name source. The old approach — dnplayer
    window titles sorted by PID, indexed by (adb_port-5554)/2 — is wrong twice:
    PID order is unrelated to instance index, and the title list is dense while
    device indices are sparse (a stopped instance leaves a gap), so every name
    after the first gap shifts. ldconsole's list2 keys the title by the real
    instance index, and each instance's ADB port is 5554 + index*2.

    list2 columns: index,title,topHwnd,bindHwnd,androidStarted,pid,vboxPid.
    Windows only; returns {} on any failure (caller falls back to a generic name).
    """
    if sys.platform != "win32":
        return {}
    exe = _find_ldconsole()
    if not exe:
        return {}
    _log(f"[ldplayer] using {exe}")
    try:
        out = subprocess.check_output(
            [exe, "list2"], timeout=5, text=True, **_no_window_flags()
        )
    except Exception:
        _log(f"[ldplayer] list2 failed: {traceback.format_exc()[:200]}")
        return {}
    _log(f"[ldplayer] list2 raw: {out.strip()!r}")
    names: dict[int, str] = {}
    for line in out.splitlines():
        parts = line.split(",")
        if len(parts) < 2:
            continue
        try:
            idx = int(parts[0])
        except ValueError:
            continue
        title = parts[1].strip()
        if title:
            names[idx] = title
    _log(f"[ldplayer] list2 idx→title: {names!r}")
    return names


def maximize_ldplayer_window(index: int, title: str | None = None):
    """No-op: instances run as VirtualBox Headless — no host GUI window to fullscreen."""
    _log(f"[ldplayer] headless mode — fullscreen skipped index={index}")


def _get_dnplayer_titles() -> list[str]:
    """Return window titles of running dnplayer instances sorted by pid, Windows only."""
    if sys.platform != "win32":
        return []
    try:
        import subprocess as _sp
        ps = (
            "Get-Process dnplayer -ErrorAction SilentlyContinue | "
            "Where-Object { $_.MainWindowTitle -ne '' } | "
            "Sort-Object Id | "
            "ForEach-Object { $_.MainWindowTitle }"
        )
        out = _sp.check_output(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            text=True, timeout=5
        )
        return [l.strip() for l in out.splitlines() if l.strip()]
    except Exception:
        return []


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
        # Authoritative index→name map (keyed by real LDPlayer instance index).
        ldconsole_names = _get_ldconsole_names()
        # Legacy fallback only if ldconsole is unavailable.
        window_titles = _get_dnplayer_titles() if not ldconsole_names else []
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
                if idx in ldconsole_names:
                    name = ldconsole_names[idx]
                elif idx < len(window_titles):
                    name = window_titles[idx]
                else:
                    name = f"LDPlayer #{idx}"
                    _log(f"[adb] no title for serial={serial} idx={idx} "
                         f"(ldconsole keys={sorted(ldconsole_names)})")
            else:
                idx = 0
                if 0 in ldconsole_names:
                    name = ldconsole_names[0]
                elif window_titles:
                    name = window_titles[0]
                else:
                    name = serial
            result.append({
                "id": f"adb:{serial}",
                "title": name,
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
    Heartbeat every 25s keeps the shell alive through idle timeouts.
    """

    def __init__(self, serial: str):
        self.serial = serial
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stopped = False
        # Pre-warm immediately — don't pay spawn cost on first tap
        self._ensure()
        # Heartbeat thread keeps adb shell alive through idle timeouts
        t = threading.Thread(target=self._heartbeat, daemon=True)
        t.start()

    def _heartbeat(self):
        while not self._stopped:
            import time
            time.sleep(25)
            if self._stopped:
                return
            with self._lock:
                if self._proc and self._proc.poll() is None:
                    try:
                        self._proc.stdin.write(b"echo .\n")
                        self._proc.stdin.flush()
                    except Exception:
                        self._proc = None
                else:
                    # Dead — pre-warm a replacement so next tap is instant
                    self._ensure_locked()

    def _ensure(self) -> bool:
        with self._lock:
            return self._ensure_locked()

    def _ensure_locked(self) -> bool:
        """Must be called with self._lock held."""
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
            if not self._ensure_locked():
                return
            try:
                self._proc.stdin.write((cmd + "\n").encode())
                self._proc.stdin.flush()
            except Exception:
                self._proc = None

    def stop(self):
        self._stopped = True
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
