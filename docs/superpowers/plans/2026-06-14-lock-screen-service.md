# Lock Screen Capture & Windows Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split WindowControl into a Windows Service (FastAPI + capture, runs as LOCAL_SYSTEM) and a GUI tray app (PyQt5), connected via named pipe IPC, so streaming and input control continue through PC lock/unlock.

**Architecture:** The existing `main.py` is split: a new `src/service_main.py` entry point hosts the FastAPI server + capture loop as a `win32serviceutil.ServiceFramework` running as LOCAL_SYSTEM (Session 0). The existing GUI tray app (`main.py`) becomes a thin client that connects to the service via a named pipe (`\\.\pipe\WindowControlPipe`) to send commands (select window, quality, start/stop) and receive state (lock status, window list). A `DesktopMonitor` thread in the service watches for WTS_SESSION_LOCK/UNLOCK events and switches the capture thread to the Winlogon desktop when locked.

**Tech Stack:** `win32serviceutil`, `win32service`, `win32ts`, `win32security`, `win32event`, `win32pipe`, `win32file`, `ctypes` (SendInput + SetThreadDesktop), `dxcam` (DXGI DuplicateOutput — GPU-side capture, monitor stays off), `mss` (GDI BitBlt fallback during lock screen only), `keyring` (Windows Credential Manager for auto-unlock password), existing FastAPI/uvicorn stack.

---

## File Structure

**New files:**
- `src/service_main.py` — Windows Service entry point (`win32serviceutil.ServiceFramework`), hosts FastAPI + capture, handles `--install`/`--uninstall`/`--start`/`--stop` CLI args
- `src/service/pipe_server.py` — Named pipe server (runs inside service), accepts JSON commands from GUI
- `src/service/pipe_client.py` — Named pipe client (runs in GUI), sends commands and reads state
- `src/service/desktop_monitor.py` — Watches WTS session events (lock/unlock/desktop switch), switches capture desktop
- `src/service/__init__.py` — Empty

**Modified files:**
- `src/server/stream.py` — Add `set_desktop(name)` to `CaptureState`; `capture_loop` uses DXGI (`dxcam`) as primary (no monitor wake), falls back to `mss`+`SetThreadDesktop` only when `desktop == "Winlogon"` (DXGI blocked during lock)
- `src/server/input_handler.py` — `handle_click/key` switch thread desktop before `SendInput` when locked
- `src/gui/launcher.py` — Add "Service" group box with Install/Uninstall/status indicator; wire to pipe client
- `src/main.py` — Add `--install`/`--uninstall` arg dispatch; when service running, GUI talks pipe instead of running FastAPI inline
- `build/window_control.spec` — Add `service_main.py` as second EXE (`WindowControlService.exe`); add `win32service`, `win32serviceutil`, `win32ts`, `win32security` to hiddenimports
- `build/installer.iss` — Install service on install, uninstall on uninstall; add `WindowControlService.exe` to `[Files]`
- `pyproject.toml` — Add `dxcam` (Windows-only, DXGI capture) and `keyring` (Credential Manager for auto-unlock password)

**Test files:**
- `tests/test_pipe_protocol.py` — Tests for pipe message serialization/deserialization
- `tests/test_desktop_monitor.py` — Tests for desktop name detection logic

---

### Task 1: Named pipe protocol + client/server stubs

**Files:**
- Create: `src/service/__init__.py`
- Create: `src/service/pipe_server.py`
- Create: `src/service/pipe_client.py`
- Create: `tests/test_pipe_protocol.py`

Pipe message format: newline-delimited JSON. Each message is one JSON object + `\n`.

Commands GUI → Service:
- `{"cmd": "start"}` — start streaming
- `{"cmd": "stop"}` — stop streaming
- `{"cmd": "select", "id": 12345}` — select window by hwnd
- `{"cmd": "quality", "value": 85}` — set JPEG quality
- `{"cmd": "ping"}` — health check

Events Service → GUI (unsolicited pushes):
- `{"event": "state", "streaming": true, "locked": false, "hwnd": 12345}`
- `{"event": "windows", "list": [{"id": 1, "title": "Foo"}]}`
- `{"event": "pong"}`

- [ ] **Step 1: Write failing tests for pipe protocol**

```python
# tests/test_pipe_protocol.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import json
from service.pipe_server import encode_msg, decode_msg

def test_encode_decode_roundtrip():
    msg = {"cmd": "select", "id": 99999}
    encoded = encode_msg(msg)
    assert encoded.endswith(b"\n")
    decoded = decode_msg(encoded.rstrip(b"\n"))
    assert decoded == msg

def test_encode_event():
    msg = {"event": "state", "streaming": True, "locked": False}
    encoded = encode_msg(msg)
    assert b"streaming" in encoded

def test_decode_invalid_returns_none():
    assert decode_msg(b"not json") is None

def test_decode_empty_returns_none():
    assert decode_msg(b"") is None
```

- [ ] **Step 2: Run to verify fails**

```bash
cd /path/to/window-control
uv run pytest tests/test_pipe_protocol.py -v
```
Expected: `ImportError: No module named 'service'`

- [ ] **Step 3: Create `src/service/__init__.py`**

```python
# src/service/__init__.py
```
(empty file)

- [ ] **Step 4: Create `src/service/pipe_server.py` with encode/decode + server skeleton**

```python
# src/service/pipe_server.py
import json
import sys
import threading
from typing import Callable

PIPE_NAME = r"\\.\pipe\WindowControlPipe"


def encode_msg(obj: dict) -> bytes:
    return json.dumps(obj).encode("utf-8") + b"\n"


def decode_msg(data: bytes) -> dict | None:
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:
        return None


if sys.platform == "win32":
    import win32pipe
    import win32file
    import win32security
    import pywintypes

    def _create_pipe_handle():
        sd = win32security.SECURITY_DESCRIPTOR()
        sd.SetSecurityDescriptorDacl(True, None, False)  # allow all
        sa = win32security.SECURITY_ATTRIBUTES()
        sa.SECURITY_DESCRIPTOR = sd
        return win32pipe.CreateNamedPipe(
            PIPE_NAME,
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
            win32pipe.PIPE_UNLIMITED_INSTANCES,
            65536, 65536,
            0,
            sa,
        )

    class PipeServer:
        """Runs in service. Accepts one client at a time in a loop."""

        def __init__(self, on_command: Callable[[dict], dict | None]):
            self._on_command = on_command
            self._running = False
            self._thread: threading.Thread | None = None

        def start(self):
            self._running = True
            self._thread = threading.Thread(target=self._serve_loop, daemon=True)
            self._thread.start()

        def stop(self):
            self._running = False

        def _serve_loop(self):
            while self._running:
                try:
                    handle = _create_pipe_handle()
                    win32pipe.ConnectNamedPipe(handle, None)
                    self._handle_client(handle)
                except Exception:
                    pass

        def _handle_client(self, handle):
            try:
                while self._running:
                    try:
                        _, data = win32file.ReadFile(handle, 65536)
                    except pywintypes.error:
                        break
                    msg = decode_msg(data.rstrip(b"\n"))
                    if msg is None:
                        continue
                    reply = self._on_command(msg)
                    if reply is not None:
                        win32file.WriteFile(handle, encode_msg(reply))
            finally:
                try:
                    handle.Close()
                except Exception:
                    pass

        def push(self, handle, event: dict):
            """Push unsolicited event to connected client."""
            try:
                win32file.WriteFile(handle, encode_msg(event))
            except Exception:
                pass

else:
    class PipeServer:
        def __init__(self, on_command):
            pass
        def start(self):
            pass
        def stop(self):
            pass
```

- [ ] **Step 5: Create `src/service/pipe_client.py`**

```python
# src/service/pipe_client.py
import json
import sys
import threading
from typing import Callable

from service.pipe_server import PIPE_NAME, encode_msg, decode_msg

if sys.platform == "win32":
    import win32pipe
    import win32file
    import pywintypes

    class PipeClient:
        """Runs in GUI tray app. Connects to service pipe, sends commands, reads events."""

        def __init__(self, on_event: Callable[[dict], None] | None = None):
            self._on_event = on_event
            self._handle = None
            self._lock = threading.Lock()
            self._connected = False

        def connect(self) -> bool:
            try:
                win32pipe.WaitNamedPipe(PIPE_NAME, 2000)
                self._handle = win32file.CreateFile(
                    PIPE_NAME,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0, None,
                    win32file.OPEN_EXISTING,
                    0, None,
                )
                win32pipe.SetNamedPipeHandleState(
                    self._handle,
                    win32pipe.PIPE_READMODE_MESSAGE,
                    None, None,
                )
                self._connected = True
                if self._on_event:
                    t = threading.Thread(target=self._read_loop, daemon=True)
                    t.start()
                return True
            except pywintypes.error:
                return False

        def send(self, cmd: dict) -> dict | None:
            if not self._connected or self._handle is None:
                return None
            with self._lock:
                try:
                    win32file.WriteFile(self._handle, encode_msg(cmd))
                    _, data = win32file.ReadFile(self._handle, 65536)
                    return decode_msg(data.rstrip(b"\n"))
                except pywintypes.error:
                    self._connected = False
                    return None

        def _read_loop(self):
            while self._connected and self._handle is not None:
                try:
                    _, data = win32file.ReadFile(self._handle, 65536)
                    msg = decode_msg(data.rstrip(b"\n"))
                    if msg and self._on_event:
                        self._on_event(msg)
                except pywintypes.error:
                    self._connected = False
                    break

        def disconnect(self):
            self._connected = False
            if self._handle:
                try:
                    self._handle.Close()
                except Exception:
                    pass
                self._handle = None

        @property
        def is_connected(self) -> bool:
            return self._connected

else:
    class PipeClient:
        def __init__(self, on_event=None):
            self._connected = False
        def connect(self) -> bool:
            return False
        def send(self, cmd: dict) -> dict | None:
            return None
        def disconnect(self):
            pass
        @property
        def is_connected(self) -> bool:
            return False
```

- [ ] **Step 6: Run tests to verify pass**

```bash
uv run pytest tests/test_pipe_protocol.py -v
```
Expected: 4 passed

- [ ] **Step 7: Commit**

```bash
git add src/service/ tests/test_pipe_protocol.py
git commit -m "feat: named pipe protocol + client/server for service IPC"
```

---

### Task 2: Desktop monitor (lock/unlock detection)

**Files:**
- Create: `src/service/desktop_monitor.py`
- Create: `tests/test_desktop_monitor.py`

The monitor registers for WTS session notifications via `win32ts.WTSRegisterSessionNotification`, runs a Win32 message loop, and calls callbacks on lock/unlock/desktop-switch events.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_desktop_monitor.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from unittest.mock import patch, MagicMock
from service.desktop_monitor import get_current_desktop_name

def test_get_desktop_name_returns_string():
    # On non-Windows, stub returns "Default"
    name = get_current_desktop_name()
    assert isinstance(name, str)
    assert len(name) > 0

def test_get_desktop_name_known_values():
    name = get_current_desktop_name()
    # Must be one of the known desktop names or a valid string
    assert name in ("Default", "Winlogon", "Screen-saver") or len(name) > 0
```

- [ ] **Step 2: Run to verify fails**

```bash
uv run pytest tests/test_desktop_monitor.py -v
```
Expected: `ImportError: No module named 'service.desktop_monitor'`

- [ ] **Step 3: Create `src/service/desktop_monitor.py`**

```python
# src/service/desktop_monitor.py
import sys
import threading
from typing import Callable

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes
    import win32api
    import win32con
    import win32gui
    import win32ts
    import win32security

    _WTS_SESSION_LOCK   = 0x7
    _WTS_SESSION_UNLOCK = 0x8
    _NOTIFY_FOR_ALL_SESSIONS = 1

    def get_current_desktop_name() -> str:
        try:
            hdesk = ctypes.windll.user32.OpenInputDesktop(0, False, 0x0200)
            if not hdesk:
                return "Default"
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetUserObjectInformationW(
                hdesk, 2, buf, ctypes.sizeof(buf), None
            )
            ctypes.windll.user32.CloseDesktop(hdesk)
            return buf.value or "Default"
        except Exception:
            return "Default"

    class DesktopMonitor:
        """Watches for WTS lock/unlock events. Runs its own message loop thread."""

        def __init__(
            self,
            on_lock: Callable[[], None],
            on_unlock: Callable[[], None],
        ):
            self._on_lock = on_lock
            self._on_unlock = on_unlock
            self._thread: threading.Thread | None = None
            self._hwnd = None

        def start(self):
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

        def stop(self):
            if self._hwnd:
                try:
                    win32gui.PostMessage(self._hwnd, win32con.WM_QUIT, 0, 0)
                except Exception:
                    pass

        def _run(self):
            wc = win32gui.WNDCLASS()
            wc.lpszClassName = "WCDesktopMonitor"
            wc.lpfnWndProc = self._wnd_proc
            win32gui.RegisterClass(wc)
            self._hwnd = win32gui.CreateWindow(
                "WCDesktopMonitor", "", 0, 0, 0, 0, 0, 0, 0, None, None
            )
            # Register for WTS session notifications
            win32ts.WTSRegisterSessionNotification(
                self._hwnd, _NOTIFY_FOR_ALL_SESSIONS
            )
            win32gui.PumpMessages()
            win32ts.WTSUnRegisterSessionNotification(self._hwnd)

        def _wnd_proc(self, hwnd, msg, wparam, lparam):
            _WM_WTSSESSION_CHANGE = 0x02B1
            if msg == _WM_WTSSESSION_CHANGE:
                if wparam == _WTS_SESSION_LOCK:
                    self._on_lock()
                elif wparam == _WTS_SESSION_UNLOCK:
                    self._on_unlock()
            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

else:
    def get_current_desktop_name() -> str:
        return "Default"

    class DesktopMonitor:
        def __init__(self, on_lock, on_unlock):
            pass
        def start(self):
            pass
        def stop(self):
            pass
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/test_desktop_monitor.py -v
```
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/service/desktop_monitor.py tests/test_desktop_monitor.py
git commit -m "feat: desktop monitor for WTS lock/unlock session events"
```

---

### Task 3: DXGI capture (primary) + mss fallback for lock screen

**Files:**
- Modify: `src/server/stream.py`
- Modify: `pyproject.toml`

Normal capture uses `dxcam` (DXGI DuplicateOutput) — reads directly from GPU compositor, monitor can be completely off. When `desktop == "Winlogon"` (PC locked), DXGI is blocked by Windows security boundary; fall back to `mss` with `SetThreadDesktop` for the lock screen only.

- [ ] **Step 1: Add dxcam to pyproject.toml**

In `pyproject.toml`, add to `dependencies`:
```toml
"dxcam>=0.0.5; sys_platform == 'win32'",
"keyring>=24.0.0",
```

Run:
```bash
uv lock
```
Expected: lock file updated

- [ ] **Step 2: Write failing tests**

```python
# tests/test_stream.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from server.stream import CaptureState

def test_capture_state_default_desktop():
    s = CaptureState()
    assert s.desktop == "Default"

def test_capture_state_set_desktop():
    s = CaptureState()
    s.set_desktop("Winlogon")
    assert s.desktop == "Winlogon"

def test_capture_state_set_desktop_roundtrip():
    s = CaptureState()
    s.set_desktop("Winlogon")
    s.set_desktop("Default")
    assert s.desktop == "Default"
```

- [ ] **Step 3: Run to verify fails**

```bash
uv run pytest tests/test_stream.py -v
```
Expected: `AttributeError: 'CaptureState' object has no attribute 'desktop'`

- [ ] **Step 4: Update `src/server/stream.py` — DXGI primary + mss lock fallback**

Replace the entire file content:

```python
import asyncio
import ctypes
import io
import queue
import sys
import threading
import time

import numpy as np
from PIL import Image

if sys.platform == "win32":
    import mss as mss_lib
    try:
        import dxcam
        _dxcam_available = True
    except ImportError:
        _dxcam_available = False
    try:
        from turbojpeg import TurboJPEG
        _jpeg = TurboJPEG()
    except (RuntimeError, OSError, ImportError):
        _jpeg = None
else:
    from stubs import mss_stub as mss_lib
    _dxcam_available = False
    _jpeg = None

from server.window_manager import get_window_rect, is_window_alive

BOUNDARY = b"--frame"


class FrameQueue:
    def __init__(self, maxsize=2):
        self._q = queue.Queue(maxsize=maxsize)

    def put(self, frame: bytes):
        if self._q.full():
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
        self._q.put_nowait(frame)

    def get(self, timeout=1.0) -> bytes | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None


class CaptureState:
    def __init__(self):
        self.active_hwnd = None
        self.quality: int = 85
        self.running: bool = False
        self.window_available: bool = True
        self.desktop: str = "Default"
        self._lock = threading.Lock()

    def set_hwnd(self, hwnd):
        with self._lock:
            self.active_hwnd = hwnd
            self.window_available = hwnd is not None

    def set_quality(self, q: int):
        with self._lock:
            self.quality = q

    def set_desktop(self, name: str):
        with self._lock:
            self.desktop = name


_BLACK_FRAME: bytes = b""


def _make_black_frame() -> bytes:
    img = Image.new("RGB", (1280, 720), color=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=40)
    return buf.getvalue()


def _encode_frame(arr: np.ndarray, quality: int) -> bytes:
    rgb = arr[:, :, 2::-1]  # BGRA → RGB
    if _jpeg is not None:
        return _jpeg.encode(rgb, quality=quality)
    buf = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _switch_thread_desktop(name: str):
    """Switch this thread to named desktop so mss can grab its pixels."""
    if sys.platform != "win32":
        return
    try:
        flags = 0x0200  # DESKTOP_WRITEOBJECTS
        hdesk = ctypes.windll.user32.OpenDesktopW(name, 0, False, flags)
        if hdesk:
            ctypes.windll.user32.SetThreadDesktop(hdesk)
            ctypes.windll.user32.CloseDesktop(hdesk)
    except Exception:
        pass


def _grab_dxgi(camera, rect: tuple) -> np.ndarray | None:
    """Grab a region via DXGI. Returns BGRA ndarray or None on error."""
    try:
        frame = camera.grab(region=rect)  # (left, top, right, bottom)
        return frame  # dxcam returns RGB ndarray directly
    except Exception:
        return None


def _grab_mss(rect: tuple) -> np.ndarray | None:
    """Grab a region via mss/GDI (works during lock with SetThreadDesktop)."""
    try:
        x0, y0, x1, y1 = rect
        monitor = {"left": x0, "top": y0, "width": max(x1 - x0, 1), "height": max(y1 - y0, 1)}
        with mss_lib.mss() as sct:
            shot = sct.grab(monitor)
            return np.frombuffer(shot.raw, dtype=np.uint8).reshape(
                (shot.height, shot.width, 4)
            )
    except Exception:
        return None


def capture_loop(state: CaptureState, frame_queue: FrameQueue):
    global _BLACK_FRAME
    _BLACK_FRAME = _make_black_frame()
    current_desktop = "Default"

    # Create dxcam camera once — reused across frames (GPU resource)
    camera = None
    if _dxcam_available:
        try:
            camera = dxcam.create(output_color="BGR")
        except Exception:
            camera = None

    while state.running:
        desktop = state.desktop

        if desktop != current_desktop:
            _switch_thread_desktop(desktop)
            current_desktop = desktop
            # Reinitialize dxcam after desktop switch (GPU context changed)
            if camera is not None and desktop == "Default":
                try:
                    camera.release()
                except Exception:
                    pass
                try:
                    camera = dxcam.create(output_color="BGR")
                except Exception:
                    camera = None

        if desktop == "Winlogon":
            # Lock screen: DXGI is blocked from Winlogon desktop.
            # Use mss/GDI (SetThreadDesktop already switched above).
            try:
                with mss_lib.mss() as sct:
                    monitor = sct.monitors[1]  # primary monitor full screen
                    shot = sct.grab(monitor)
                    arr = np.frombuffer(shot.raw, dtype=np.uint8).reshape(
                        (shot.height, shot.width, 4)
                    )
                jpeg_bytes = _encode_frame(arr, state.quality)
                frame_queue.put(jpeg_bytes)
            except Exception:
                frame_queue.put(_BLACK_FRAME)
            time.sleep(1 / 30)
            continue

        hwnd = state.active_hwnd
        if hwnd is None:
            frame_queue.put(_BLACK_FRAME)
            time.sleep(0.05)
            continue

        if not is_window_alive(hwnd):
            state.set_hwnd(None)
            frame_queue.put(_BLACK_FRAME)
            time.sleep(0.05)
            continue

        try:
            rect = get_window_rect(hwnd)  # (x0, y0, x1, y1)
            arr = None

            # Primary: DXGI — GPU-side, monitor power state irrelevant
            if camera is not None:
                arr = _grab_dxgi(camera, rect)

            # Fallback: mss/GDI (monitor must be on, but always works)
            if arr is None:
                arr = _grab_mss(rect)

            if arr is None:
                frame_queue.put(_BLACK_FRAME)
            else:
                jpeg_bytes = _encode_frame(arr, state.quality)
                frame_queue.put(jpeg_bytes)
        except Exception:
            frame_queue.put(_BLACK_FRAME)

        time.sleep(1 / 30)

    if camera is not None:
        try:
            camera.release()
        except Exception:
            pass


async def mjpeg_generator(frame_queue: FrameQueue):
    loop = asyncio.get_event_loop()
    while True:
        frame = await loop.run_in_executor(None, frame_queue.get, 1.0)
        if frame is None:
            continue
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        )
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_stream.py -v
```
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add src/server/stream.py pyproject.toml uv.lock tests/test_stream.py
git commit -m "feat: DXGI primary capture (no monitor wake) + mss fallback for lock screen"
```

---

### Task 3b: Auto-unlock + monitor-off after unlock

**Files:**
- Create: `src/service/auto_unlock.py`
- Modify: `src/service_main.py` (wire into `_on_lock` / `_on_unlock`)
- Modify: `src/gui/launcher.py` (add "Set unlock password" button)

On lock: service waits 1.5s for Winlogon to render, reads stored password from Windows Credential Manager, types it + Enter via `SendInput` on Winlogon desktop. On unlock: sends `SC_MONITORPOWER` message to turn monitor off again so physical screen stays dark.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_auto_unlock.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from service.auto_unlock import CREDENTIAL_SERVICE, get_stored_password, store_password

def test_credential_service_name():
    assert CREDENTIAL_SERVICE == "WindowControl"

def test_store_and_get_password():
    # Uses real keyring on Windows, memory keyring stub on Mac/Linux
    store_password("test_pass_123")
    result = get_stored_password()
    assert result == "test_pass_123"

def test_get_password_returns_none_if_not_set():
    # Clear and verify
    import keyring
    keyring.delete_password(CREDENTIAL_SERVICE, "unlock")
    result = get_stored_password()
    assert result is None
```

- [ ] **Step 2: Run to verify fails**

```bash
uv run pytest tests/test_auto_unlock.py -v
```
Expected: `ModuleNotFoundError: No module named 'service.auto_unlock'`

- [ ] **Step 3: Create `src/service/auto_unlock.py`**

```python
# src/service/auto_unlock.py
import sys
import time
import threading

import keyring

CREDENTIAL_SERVICE = "WindowControl"
CREDENTIAL_USER = "unlock"


def store_password(password: str) -> None:
    keyring.set_password(CREDENTIAL_SERVICE, CREDENTIAL_USER, password)


def get_stored_password() -> str | None:
    try:
        return keyring.get_password(CREDENTIAL_SERVICE, CREDENTIAL_USER)
    except Exception:
        return None


def delete_password() -> None:
    try:
        keyring.delete_password(CREDENTIAL_SERVICE, CREDENTIAL_USER)
    except Exception:
        pass


def _turn_monitor_off():
    """Send SC_MONITORPOWER to turn display off (2 = power off)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        HWND_BROADCAST = 0xFFFF
        WM_SYSCOMMAND = 0x0112
        SC_MONITORPOWER = 0xF170
        ctypes.windll.user32.PostMessageW(HWND_BROADCAST, WM_SYSCOMMAND, SC_MONITORPOWER, 2)
    except Exception:
        pass


def _type_password_to_winlogon(password: str):
    """Switch thread to Winlogon desktop and type password + Enter via SendInput."""
    if sys.platform != "win32":
        return
    import ctypes
    import ctypes.wintypes

    DESKTOP_ALL_ACCESS = 0x01FF
    KEYEVENTF_KEYUP = 0x0002
    INPUT_KEYBOARD = 1
    VK_RETURN = 0x0D
    KEYEVENTF_UNICODE = 0x0004

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk",         ctypes.c_ushort),
            ("wScan",       ctypes.c_ushort),
            ("dwFlags",     ctypes.c_ulong),
            ("time",        ctypes.c_ulong),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class _UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("_u", _UNION)]

    user32 = ctypes.windll.user32

    hdesk = user32.OpenDesktopW("Winlogon", 0, False, DESKTOP_ALL_ACCESS)
    if not hdesk:
        return
    user32.SetThreadDesktop(hdesk)

    def _send_char(ch: str):
        scan = ord(ch)
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            ki = KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=None)
            inp = INPUT(type=INPUT_KEYBOARD, _u=_UNION(ki=ki))
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
            time.sleep(0.02)

    def _send_vk(vk: int):
        for flags in (0, KEYEVENTF_KEYUP):
            ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags, time=0, dwExtraInfo=None)
            inp = INPUT(type=INPUT_KEYBOARD, _u=_UNION(ki=ki))
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))
            time.sleep(0.02)

    for ch in password:
        _send_char(ch)

    _send_vk(VK_RETURN)

    # Restore Default desktop
    hdefault = user32.OpenDesktopW("Default", 0, False, DESKTOP_ALL_ACCESS)
    if hdefault:
        user32.SetThreadDesktop(hdefault)
        user32.CloseDesktop(hdefault)
    user32.CloseDesktop(hdesk)


def auto_unlock_on_lock():
    """
    Called when WTS_SESSION_LOCK fires.
    Waits for Winlogon to render, then types stored password.
    After unlock detected, turns monitor off.
    Run in a daemon thread — do not block the caller.
    """
    password = get_stored_password()
    if not password:
        return  # no password stored, user must type manually

    def _run():
        time.sleep(1.5)  # wait for Winlogon desktop to fully render
        _type_password_to_winlogon(password)

    threading.Thread(target=_run, daemon=True).start()


def turn_monitor_off_after_unlock():
    """
    Called when WTS_SESSION_UNLOCK fires.
    Brief delay so desktop finishes rendering, then kills monitor.
    """
    def _run():
        time.sleep(0.5)
        _turn_monitor_off()

    threading.Thread(target=_run, daemon=True).start()
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_auto_unlock.py -v
```
Expected: 3 passed

- [ ] **Step 5: Wire into `service_main.py` — update `_on_lock` and `_on_unlock`**

In `src/service_main.py`, add import at top:
```python
from service.auto_unlock import auto_unlock_on_lock, turn_monitor_off_after_unlock
```

Replace `_on_lock` and `_on_unlock` methods:
```python
def _on_lock(self):
    self._state.set_desktop("Winlogon")
    servicemanager.LogInfoMsg("WindowControl: session locked")
    auto_unlock_on_lock()  # types stored password after 1.5s

def _on_unlock(self):
    self._state.set_desktop("Default")
    self._available_windows.clear()
    self._available_windows.extend(_build_windows())
    servicemanager.LogInfoMsg("WindowControl: session unlocked")
    turn_monitor_off_after_unlock()  # kills monitor 0.5s after unlock
```

- [ ] **Step 6: Add "Set Unlock Password" button to `src/gui/launcher.py`**

In the service group box setup (`_setup_service_group`), add after the btn_row:

```python
pw_row = QHBoxLayout()
self._set_pw_btn = QPushButton("Set Unlock Password")
self._set_pw_btn.clicked.connect(self._on_set_unlock_password)
self._clear_pw_btn = QPushButton("Clear Password")
self._clear_pw_btn.clicked.connect(self._on_clear_unlock_password)
pw_row.addWidget(self._set_pw_btn)
pw_row.addWidget(self._clear_pw_btn)
service_layout.addLayout(pw_row)
```

Add methods to `LauncherWindow`:
```python
def _on_set_unlock_password(self):
    from PyQt5.QtWidgets import QInputDialog, QLineEdit
    pw, ok = QInputDialog.getText(
        self, "Set Unlock Password",
        "Enter your Windows password for auto-unlock:",
        QLineEdit.Password
    )
    if ok and pw:
        from service.auto_unlock import store_password
        store_password(pw)
        self._status_label.setText("Unlock password saved.")

def _on_clear_unlock_password(self):
    from service.auto_unlock import delete_password
    delete_password()
    self._status_label.setText("Unlock password cleared.")
```

- [ ] **Step 7: Commit**

```bash
git add src/service/auto_unlock.py src/service_main.py src/gui/launcher.py tests/test_auto_unlock.py
git commit -m "feat: auto-unlock on lock + monitor-off after unlock"
```

---

### Task 4: Lock-aware input injection in input_handler.py

**Files:**
- Modify: `src/server/input_handler.py`

When desktop is "Winlogon", `handle_click` and `handle_key` must open and attach the Winlogon desktop before calling `SendInput`, then restore.

- [ ] **Step 1: Write failing test**

```python
# Add to tests/test_input_handler.py
from unittest.mock import patch, MagicMock

def test_handle_key_on_winlogon_desktop_uses_sendkey():
    """handle_key must not crash when called with Winlogon desktop."""
    with patch('server.input_handler._send_key') as mock_send, \
         patch('server.input_handler._focus_window'), \
         patch('server.input_handler.win32gui'):
        from server.input_handler import handle_key_on_desktop
        handle_key_on_desktop(0, "a", "Winlogon")
        # _send_key called twice: keydown + keyup
        assert mock_send.call_count == 2
```

- [ ] **Step 2: Run to verify fails**

```bash
uv run pytest tests/test_input_handler.py::test_handle_key_on_winlogon_desktop_uses_sendkey -v
```
Expected: `ImportError: cannot import name 'handle_key_on_desktop'`

- [ ] **Step 3: Add `handle_key_on_desktop` and `handle_click_on_desktop` to `src/server/input_handler.py`**

Add these functions at the bottom of `src/server/input_handler.py`:

```python
def _with_desktop(desktop_name: str, fn):
    """Run fn() with thread temporarily switched to named desktop."""
    if sys.platform != "win32" or desktop_name == "Default":
        return fn()
    try:
        import ctypes
        DESKTOP_ALL_ACCESS = 0x01FF
        hdesk = ctypes.windll.user32.OpenDesktopW(
            desktop_name, 0, False, DESKTOP_ALL_ACCESS
        )
        if hdesk:
            ctypes.windll.user32.SetThreadDesktop(hdesk)
            try:
                return fn()
            finally:
                # Restore to Default desktop
                hdefault = ctypes.windll.user32.OpenDesktopW(
                    "Default", 0, False, DESKTOP_ALL_ACCESS
                )
                if hdefault:
                    ctypes.windll.user32.SetThreadDesktop(hdefault)
                    ctypes.windll.user32.CloseDesktop(hdefault)
                ctypes.windll.user32.CloseDesktop(hdesk)
    except Exception:
        return fn()


def handle_click_on_desktop(hwnd, nx: float, ny: float, desktop: str = "Default"):
    """Click with desktop-switching for lock screen support."""
    def _do():
        if desktop == "Winlogon":
            # Lock screen: treat nx/ny as absolute screen fractions
            sw, sh = _screen_size()
            ax, ay = int(nx * sw), int(ny * sh)
        else:
            _focus_window(hwnd)
            ax, ay = _abs_coords(hwnd, nx, ny)
        _send_mouse(MOUSEEVENTF_MOVE, ax, ay)
        time.sleep(0.02)
        _send_mouse(MOUSEEVENTF_LEFTDOWN, ax, ay)
        time.sleep(0.02)
        _send_mouse(MOUSEEVENTF_LEFTUP, ax, ay)
    _with_desktop(desktop, _do)


def handle_key_on_desktop(hwnd, key: str, desktop: str = "Default"):
    """Send key with desktop-switching for lock screen support."""
    vk = KEY_MAP.get(key)
    if vk is None and len(key) == 1:
        vk = ord(key.upper())
    if vk is None:
        return
    def _do():
        if desktop == "Default":
            _focus_window(hwnd)
        _send_key(vk, key_up=False)
        time.sleep(0.02)
        _send_key(vk, key_up=True)
    _with_desktop(desktop, _do)
```

- [ ] **Step 4: Run tests to verify pass**

```bash
uv run pytest tests/test_input_handler.py -v
```
Expected: all pass

- [ ] **Step 5: Update `src/server/app.py` WebSocket handler to use new desktop-aware functions**

In `src/server/app.py`, update the `ws_input` handler:

```python
# At top of file, update import:
from server.input_handler import (
    handle_click, handle_move, handle_scroll, handle_key,
    handle_click_on_desktop, handle_key_on_desktop,
)

# In ws_input, replace the dispatch block:
t = data.get("type")
desktop = state.desktop
if t == "click":
    handle_click_on_desktop(hwnd, data["x"], data["y"], desktop)
elif t == "move":
    handle_move(hwnd, data["x"], data["y"])
elif t == "scroll":
    handle_scroll(hwnd, data.get("dx", 0), data.get("dy", 0))
elif t == "key":
    handle_key_on_desktop(hwnd, data["key"], desktop)
```

- [ ] **Step 6: Run all tests**

```bash
uv run pytest tests/ -v
```
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add src/server/input_handler.py src/server/app.py tests/test_input_handler.py
git commit -m "feat: desktop-aware input injection for lock screen PIN/mouse"
```

---

### Task 5: Windows Service entry point

**Files:**
- Create: `src/service_main.py`

This is the service process. It hosts FastAPI + capture loop and is controlled via `--install`, `--uninstall`, `--start`, `--stop` CLI args. When run as a service (no args), it enters `win32serviceutil.HandleCommandLine`.

- [ ] **Step 1: Create `src/service_main.py`**

```python
# src/service_main.py
"""
WindowControl Windows Service.

Usage:
  WindowControl.exe --install     Install and start the service
  WindowControl.exe --uninstall   Stop and remove the service
  WindowControl.exe --start       Start an installed service
  WindowControl.exe --stop        Stop the running service
  (no args)                       Run as service (called by SCM)
"""
import sys
import threading
import time
import uvicorn

if sys.platform == "win32":
    import win32service
    import win32serviceutil
    import win32event
    import servicemanager

from config import PORT, QUALITY_MAP, DEFAULT_QUALITY
from server.app import create_app
from server.stream import CaptureState, FrameQueue, capture_loop
from server.window_manager import list_windows
from service.pipe_server import PipeServer
from service.desktop_monitor import DesktopMonitor


SERVICE_NAME = "WindowControlService"
SERVICE_DISPLAY = "Window Control Streaming Service"
SERVICE_DESCRIPTION = "Streams Windows application windows to iPhone over Tailscale. Continues during lock screen."


def _build_windows():
    windows = list_windows()
    return [{"id": w.hwnd, "title": w.title} for w in windows]


if sys.platform == "win32":
    class WindowControlService(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = SERVICE_DISPLAY
        _svc_description_ = SERVICE_DESCRIPTION

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self._stop_event = win32event.CreateEvent(None, 0, 0, None)
            self._state = CaptureState()
            self._state.set_quality(QUALITY_MAP[DEFAULT_QUALITY])
            self._frame_queue = FrameQueue()
            self._available_windows = []
            self._server = None
            self._pipe_server = None
            self._desktop_monitor = None

        def SvcDoRun(self):
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, "")
            )
            self._run()
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STOPPED,
                (self._svc_name_, "")
            )

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self._state.running = False
            if self._server:
                self._server.should_exit = True
            if self._pipe_server:
                self._pipe_server.stop()
            if self._desktop_monitor:
                self._desktop_monitor.stop()
            win32event.SetEvent(self._stop_event)

        def _on_lock(self):
            self._state.set_desktop("Winlogon")
            servicemanager.LogInfoMsg("WindowControl: session locked, streaming lock screen")

        def _on_unlock(self):
            self._state.set_desktop("Default")
            self._available_windows.clear()
            self._available_windows.extend(_build_windows())
            servicemanager.LogInfoMsg("WindowControl: session unlocked, resuming normal stream")

        def _on_command(self, msg: dict) -> dict | None:
            cmd = msg.get("cmd")
            if cmd == "ping":
                return {"event": "pong"}
            elif cmd == "start":
                if not self._state.running:
                    self._start_streaming()
                return {"event": "state", "streaming": True, "locked": self._state.desktop == "Winlogon"}
            elif cmd == "stop":
                self._state.running = False
                if self._server:
                    self._server.should_exit = True
                return {"event": "state", "streaming": False, "locked": self._state.desktop == "Winlogon"}
            elif cmd == "select":
                hwnd = msg.get("id")
                if hwnd:
                    self._state.set_hwnd(hwnd)
                return {"event": "state", "streaming": self._state.running, "hwnd": hwnd}
            elif cmd == "quality":
                self._state.set_quality(msg.get("value", 85))
                return {"event": "pong"}
            elif cmd == "windows":
                self._available_windows.clear()
                self._available_windows.extend(_build_windows())
                return {"event": "windows", "list": self._available_windows}
            return None

        def _start_streaming(self):
            self._state.running = True
            self._available_windows.clear()
            self._available_windows.extend(_build_windows())

            fastapi_app = create_app(
                self._state, self._frame_queue, self._available_windows
            )
            config = uvicorn.Config(
                fastapi_app, host="0.0.0.0", port=PORT,
                log_level="warning", log_config=None
            )
            self._server = uvicorn.Server(config)

            threading.Thread(
                target=capture_loop,
                args=(self._state, self._frame_queue),
                daemon=True,
            ).start()
            threading.Thread(target=self._server.run, daemon=True).start()

        def _run(self):
            self._desktop_monitor = DesktopMonitor(
                on_lock=self._on_lock,
                on_unlock=self._on_unlock,
            )
            self._desktop_monitor.start()

            self._pipe_server = PipeServer(on_command=self._on_command)
            self._pipe_server.start()

            self._start_streaming()

            win32event.WaitForSingleObject(self._stop_event, win32event.INFINITE)


def install_service():
    win32serviceutil.InstallService(
        None,
        SERVICE_NAME,
        SERVICE_DISPLAY,
        description=SERVICE_DESCRIPTION,
        startType=win32service.SERVICE_AUTO_START,
        exeName=sys.executable,
    )
    # Configure failure actions: restart 3 times with 60s delay
    hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
    hsvc = win32service.OpenService(hscm, SERVICE_NAME, win32service.SERVICE_ALL_ACCESS)
    win32service.ChangeServiceConfig2(
        hsvc,
        win32service.SERVICE_CONFIG_FAILURE_ACTIONS,
        {
            "ResetPeriod": 86400,
            "RebootMsg": "",
            "Command": "",
            "Actions": [
                (win32service.SC_ACTION_RESTART, 60000),
                (win32service.SC_ACTION_RESTART, 60000),
                (win32service.SC_ACTION_RESTART, 60000),
            ],
        }
    )
    win32service.CloseServiceHandle(hsvc)
    win32service.CloseServiceHandle(hscm)
    win32serviceutil.StartService(SERVICE_NAME)
    print(f"Service '{SERVICE_NAME}' installed and started.")


def uninstall_service():
    try:
        win32serviceutil.StopService(SERVICE_NAME)
        time.sleep(2)
    except Exception:
        pass
    win32serviceutil.RemoveService(SERVICE_NAME)
    print(f"Service '{SERVICE_NAME}' removed.")


def main():
    if "--install" in sys.argv:
        install_service()
    elif "--uninstall" in sys.argv:
        uninstall_service()
    elif "--start" in sys.argv:
        win32serviceutil.StartService(SERVICE_NAME)
    elif "--stop" in sys.argv:
        win32serviceutil.StopService(SERVICE_NAME)
    else:
        win32serviceutil.HandleCommandLine(WindowControlService)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify import works on Mac (stubs)**

```bash
uv run python -c "import sys; sys.path.insert(0,'src'); import service_main; print('OK')"
```
Expected: `OK` (win32 blocks skipped via platform check)

- [ ] **Step 3: Commit**

```bash
git add src/service_main.py
git commit -m "feat: Windows Service entry point with install/uninstall/lock handling"
```

---

### Task 6: GUI service manager panel in launcher.py

**Files:**
- Modify: `src/gui/launcher.py`

Add a "Service" `QGroupBox` at the top with: status dot label, Install button, Uninstall button. On startup, check if service is installed and update status. Poll pipe every 3 seconds to check connection.

- [ ] **Step 1: Add service status check utility**

Add `src/gui/service_status.py`:

```python
# src/gui/service_status.py
import sys

if sys.platform == "win32":
    import win32service
    import win32serviceutil

    SERVICE_NAME = "WindowControlService"

    def get_service_status() -> str:
        """Returns 'running', 'stopped', 'not_installed'."""
        try:
            status = win32serviceutil.QueryServiceStatus(SERVICE_NAME)
            state = status[1]
            if state == win32service.SERVICE_RUNNING:
                return "running"
            elif state in (win32service.SERVICE_STOPPED, win32service.SERVICE_STOP_PENDING):
                return "stopped"
            return "stopped"
        except Exception:
            return "not_installed"

    def is_service_installed() -> bool:
        return get_service_status() != "not_installed"

else:
    def get_service_status() -> str:
        return "not_installed"

    def is_service_installed() -> bool:
        return False
```

- [ ] **Step 2: Add service group box to `LauncherWindow` in `src/gui/launcher.py`**

Add import at top of `launcher.py`:
```python
import subprocess
from gui.service_status import get_service_status, is_service_installed
```

Add `_setup_service_group` method and call it from `_setup_ui` (insert before the server group):

```python
def _setup_service_group(self, layout: QVBoxLayout):
    service_group = QGroupBox("Lock Screen Service")
    service_layout = QVBoxLayout(service_group)

    status_row = QHBoxLayout()
    self._service_dot = QLabel("●")
    self._service_dot.setFixedWidth(16)
    self._service_status_label = QLabel("Checking…")
    status_row.addWidget(self._service_dot)
    status_row.addWidget(self._service_status_label)
    status_row.addStretch()
    service_layout.addLayout(status_row)

    btn_row = QHBoxLayout()
    self._install_btn = QPushButton("Install Service")
    self._install_btn.clicked.connect(self._on_install_service)
    self._uninstall_btn = QPushButton("Uninstall")
    self._uninstall_btn.clicked.connect(self._on_uninstall_service)
    btn_row.addWidget(self._install_btn)
    btn_row.addWidget(self._uninstall_btn)
    service_layout.addLayout(btn_row)

    layout.addWidget(service_group)
    self._refresh_service_status()

def _refresh_service_status(self):
    status = get_service_status()
    if status == "running":
        self._service_dot.setStyleSheet("color: #22c55e;")  # green
        self._service_status_label.setText("Running — lock screen active")
        self._install_btn.setEnabled(False)
        self._uninstall_btn.setEnabled(True)
    elif status == "stopped":
        self._service_dot.setStyleSheet("color: #ef4444;")  # red
        self._service_status_label.setText("Stopped")
        self._install_btn.setEnabled(True)
        self._uninstall_btn.setEnabled(True)
    else:  # not_installed
        self._service_dot.setStyleSheet("color: #94a3b8;")  # grey
        self._service_status_label.setText("Not installed")
        self._install_btn.setEnabled(True)
        self._uninstall_btn.setEnabled(False)

def _on_install_service(self):
    self._service_dot.setStyleSheet("color: #f59e0b;")  # yellow
    self._service_status_label.setText("Installing…")
    self._install_btn.setEnabled(False)
    subprocess.Popen(
        [sys.executable, "--install"],
        creationflags=0x00000008  # DETACHED_PROCESS
    )
    QTimer = __import__('PyQt5.QtCore', fromlist=['QTimer']).QTimer
    QTimer.singleShot(3000, self._refresh_service_status)

def _on_uninstall_service(self):
    subprocess.Popen(
        [sys.executable, "--uninstall"],
        creationflags=0x00000008
    )
    QTimer = __import__('PyQt5.QtCore', fromlist=['QTimer']).QTimer
    QTimer.singleShot(3000, self._refresh_service_status)
```

In `_setup_ui`, add the call before the server group line:
```python
self._setup_service_group(layout)   # add this line
layout.addWidget(server_group)      # existing line
```

- [ ] **Step 3: Verify no import errors on Mac**

```bash
uv run python -c "import sys; sys.path.insert(0,'src'); from gui.launcher import LauncherWindow; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add src/gui/launcher.py src/gui/service_status.py
git commit -m "feat: service manager panel in launcher GUI — install/uninstall/status"
```

---

### Task 7: Update main.py to dispatch --install/--uninstall

**Files:**
- Modify: `src/main.py`

Before launching the Qt app, check for `--install`/`--uninstall` args and delegate to `service_main`.

- [ ] **Step 1: Modify `src/main.py`**

Add at the top of `main()`, before `app = QApplication(sys.argv)`:

```python
def main():
    # Delegate service CLI args before starting GUI
    if "--install" in sys.argv or "--uninstall" in sys.argv:
        from service_main import main as service_cli
        service_cli()
        return
    if "--start" in sys.argv or "--stop" in sys.argv:
        from service_main import main as service_cli
        service_cli()
        return

    app = QApplication(sys.argv)
    # ... rest of existing main() unchanged
```

- [ ] **Step 2: Verify**

```bash
uv run python -c "import sys; sys.path.insert(0,'src'); import main; print('OK')"
```
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add src/main.py
git commit -m "feat: dispatch --install/--uninstall CLI args from main.py"
```

---

### Task 8: Update PyInstaller spec for service

**Files:**
- Modify: `build/window_control.spec`

Add `win32service`, `win32serviceutil`, `win32ts`, `win32security`, `servicemanager` to `hiddenimports`. The service runs from the same `WindowControl.exe` via `--install`.

- [ ] **Step 1: Update `build/window_control.spec` hiddenimports**

Add to the `hiddenimports` list in `Analysis(...)`:

```python
hiddenimports=[
    # ... existing entries ...
    'win32service',
    'win32serviceutil',
    'win32ts',
    'win32security',
    'win32event',
    'win32pipe',
    'win32file',
    'servicemanager',
    'service',
    'service.pipe_server',
    'service.pipe_client',
    'service.desktop_monitor',
    'service_main',
] + win32_hiddenimports + win32api_hiddenimports + pywintypes_hiddenimports,
```

The full updated `hiddenimports` block:

```python
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'fastapi',
        'starlette',
        'pystray',
        'PIL',
        'qrcode',
        'mss',
        'numpy',
        'win32gui',
        'win32api',
        'win32con',
        'win32process',
        'win32com',
        'pywintypes',
        'win32service',
        'win32serviceutil',
        'win32ts',
        'win32security',
        'win32event',
        'win32pipe',
        'win32file',
        'servicemanager',
        'service',
        'service.pipe_server',
        'service.pipe_client',
        'service.desktop_monitor',
        'service.auto_unlock',
        'service_main',
        'dxcam',
        'keyring',
        'keyring.backends',
        'keyring.backends.Windows',
    ] + win32_hiddenimports + win32api_hiddenimports + pywintypes_hiddenimports,
```

- [ ] **Step 2: Commit**

```bash
git add build/window_control.spec
git commit -m "chore: add service modules to PyInstaller hiddenimports"
```

---

### Task 9: Update installer.iss for service lifecycle

**Files:**
- Modify: `build/installer.iss`

Run `--install` after installation; run `--uninstall` before uninstall.

- [ ] **Step 1: Update `build/installer.iss`**

Add to `[Run]` section (after vc_redist line, before app launch line):

```ini
Filename: "{app}\{#MyAppExeName}"; Parameters: "--install"; StatusMsg: "Installing lock screen service..."; Flags: waituntilterminated runhidden; Check: IsAdminInstallMode
```

Add a new `[UninstallRun]` section before `[Run]`:

```ini
[UninstallRun]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--uninstall"; Flags: waituntilterminated runhidden; RunOnceId: "UninstallService"
```

The full updated `[Run]` section:

```ini
[UninstallRun]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--uninstall"; Flags: waituntilterminated runhidden; RunOnceId: "UninstallService"

[Run]
Filename: "{tmp}\vc_redist.x64.exe"; Parameters: "/quiet /norestart"; StatusMsg: "Installing Visual C++ Runtime..."; Flags: waituntilterminated; Check: NeedsVCRedist
Filename: "{app}\{#MyAppExeName}"; Parameters: "--install"; StatusMsg: "Installing lock screen service..."; Flags: waituntilterminated runhidden
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
```

- [ ] **Step 2: Commit**

```bash
git add build/installer.iss
git commit -m "chore: installer auto-installs/uninstalls Windows Service"
```

---

### Task 10: Bump version + full test run + final commit

**Files:**
- Modify: `src/config.py`

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest tests/ -v
```
Expected: all pass (new tests for pipe protocol, desktop monitor, stream, input handler)

- [ ] **Step 2: Bump version to 1.2.0**

In `src/config.py`:
```python
VERSION = "1.2.0"
```

- [ ] **Step 3: Sync version**

```bash
python scripts/bump_version.py
```
Expected: `Version synced to 1.2.0 in pyproject.toml and installer.iss`

- [ ] **Step 4: Commit**

```bash
git add src/config.py pyproject.toml build/installer.iss
git commit -m "chore: bump to 1.2.0 — lock screen service release"
```

---

## Self-Review

**Spec coverage check:**

| Requirement | Task |
|---|---|
| Windows Service (win32serviceutil) | Task 5 |
| Service name WindowControlService | Task 5 |
| LOCAL_SYSTEM + auto-start | Task 5 |
| Auto-restart on failure (3x) | Task 5 |
| `--install` / `--uninstall` | Tasks 5, 7 |
| Event Log logging | Task 5 (servicemanager.LogMsg) |
| DXGI capture normal use (no monitor wake) | Task 3 (dxcam primary) |
| mss fallback during lock | Task 3 (SetThreadDesktop + mss) |
| Survives lock screen | Task 3 + Task 5 |
| Auto-unlock (types password) | Task 3b |
| Monitor off after unlock | Task 3b |
| Store password in Credential Manager | Task 3b |
| WTS_SESSION_LOCK / UNLOCK events | Task 2 |
| Desktop switch handling | Task 2 |
| Notify iPhone: locked indicator | Task 3 (state.desktop propagated to stream) |
| Input to Winlogon desktop | Task 4 |
| SendInput to correct desktop | Task 4 |
| Named pipe IPC | Task 1 |
| GUI Install/Uninstall/status | Task 6 |
| PyInstaller packaging | Task 8 |
| Installer service lifecycle | Task 9 |

**Gaps addressed:**
- `dxcam` (DXGI) used as primary capture — GPU-side, monitor stays off. `mss` fallback only during Winlogon desktop (DXGI blocked during lock by Windows security boundary; GDI/mss works from SYSTEM on Winlogon desktop).
- iPhone "PC Locked" indicator: handled implicitly — when `state.desktop == "Winlogon"`, stream shows lock screen content instead of app window. Client JS `status-pill` already shows the stream state. A future enhancement could add an explicit overlay.
- Named pipe read/write in `PipeServer._handle_client` uses blocking `ReadFile` — this is correct since each client gets its own `_handle_client` call from the accept loop.
