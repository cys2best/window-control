import asyncio
import ctypes
import io
import os
import queue
import sys
import threading
import time
import traceback

import numpy as np
from PIL import Image


def _log(msg: str):
    for _p in [r"C:\ProgramData\WindowControl", r"C:\Windows\Temp"]:
        try:
            os.makedirs(_p, exist_ok=True)
            with open(os.path.join(_p, "service_crash.log"), "a") as f:
                f.write(msg + "\n")
            return
        except Exception:
            continue

if sys.platform == "win32":
    _mss_lib = None
    _dxcam_available = None  # None = not yet checked
    _jpeg = None

    def _ensure_capture_libs():
        global _mss_lib, _dxcam_available, _jpeg
        if _mss_lib is not None:
            return
        try:
            import mss as mss_lib
            _mss_lib = mss_lib
            _log("[capture] mss loaded OK")
        except Exception:
            _log(f"[capture] mss load FAILED: {traceback.format_exc()}")
            raise
        try:
            import dxcam
            _dxcam_available = True
            _log("[capture] dxcam loaded OK")
        except Exception:
            _dxcam_available = False
            _log(f"[capture] dxcam unavailable (ok): {traceback.format_exc()[:600]}")
        try:
            from turbojpeg import TurboJPEG
            _jpeg = TurboJPEG()
            _log("[capture] TurboJPEG loaded OK")
        except Exception:
            _jpeg = None
            _log(f"[capture] TurboJPEG unavailable (ok): {traceback.format_exc()[:200]}")
else:
    from stubs import mss_stub as _mss_lib
    _dxcam_available = False
    _jpeg = None

    def _ensure_capture_libs():
        pass

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
_LOCKED_FRAME: bytes = b""
_hwinsta = None  # keep WinSta0 handle alive for the process lifetime


def _make_black_frame() -> bytes:
    img = Image.new("RGB", (1280, 720), color=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=40)
    return buf.getvalue()


def _make_locked_frame() -> bytes:
    img = Image.new("RGB", (1280, 720), color=(15, 15, 35))
    try:
        from PIL import ImageDraw
        draw = ImageDraw.Draw(img)
        # Lock icon (simple rectangle + arc)
        cx, cy = 640, 300
        draw.rectangle([cx-30, cy, cx+30, cy+40], fill=(180, 180, 200))
        draw.arc([cx-22, cy-30, cx+22, cy+10], start=0, end=180, fill=(180, 180, 200), width=8)
        # Text
        draw.text((640, 380), "Screen Locked", fill=(160, 160, 180), anchor="mm")
        draw.text((640, 410), "Unlock PC to resume stream", fill=(100, 100, 120), anchor="mm")
    except Exception:
        pass
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=60)
    return buf.getvalue()


def _encode_frame(arr: np.ndarray, quality: int) -> bytes:
    rgb = arr[:, :, 2::-1]  # BGRA → RGB
    if _jpeg is not None:
        return _jpeg.encode(rgb, quality=quality)
    buf = io.BytesIO()
    Image.fromarray(rgb, "RGB").save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def _switch_thread_desktop(name: str):
    """Switch this thread's window station + desktop so mss/BitBlt can grab pixels.

    SYSTEM services run in Session 0 with no window station. We must explicitly
    open WinSta0 (the interactive station) and then the named desktop within it.
    The WinSta0 handle must be kept alive globally — if it closes, the process
    reverts to the null station and BitBlt silently fails (GetLastError=0).
    """
    global _hwinsta
    if sys.platform != "win32":
        return
    try:
        # Open the interactive window station — SYSTEM can open it but doesn't own it.
        # Store in global so handle stays alive; closing it reverts the process station.
        WINSTA_ALL_ACCESS = 0x037F
        hwinsta = ctypes.windll.user32.OpenWindowStationW("WinSta0", False, WINSTA_ALL_ACCESS)
        if hwinsta:
            ctypes.windll.user32.SetProcessWindowStation(hwinsta)
            _hwinsta = hwinsta  # keep alive — GC would close handle and revert station
            _log(f"[desktop] SetProcessWindowStation(WinSta0) OK")
        else:
            _log(f"[desktop] OpenWindowStation(WinSta0) failed err={ctypes.GetLastError()}")

        DESKTOP_ALL_ACCESS = 0x01FF
        hdesk = ctypes.windll.user32.OpenDesktopW(name, 0, False, DESKTOP_ALL_ACCESS)
        if hdesk:
            ctypes.windll.user32.SetThreadDesktop(hdesk)
            _log(f"[desktop] SetThreadDesktop({name}) OK")
        else:
            _log(f"[desktop] OpenDesktop({name}) failed err={ctypes.GetLastError()}")
    except Exception:
        _log(f"[desktop] _switch_thread_desktop({name}) exception: {traceback.format_exc()[:300]}")


def _grab_dxgi(camera, rect: tuple) -> np.ndarray | None:
    """Grab a region via DXGI. Returns BGR ndarray or None on error."""
    try:
        frame = camera.grab(region=rect)  # (left, top, right, bottom)
        return frame  # dxcam returns BGR ndarray directly
    except Exception:
        return None


def _grab_mss(rect: tuple) -> np.ndarray | None:
    """Grab a region via mss/GDI (works during lock with SetThreadDesktop)."""
    try:
        x0, y0, x1, y1 = rect
        monitor = {"left": x0, "top": y0, "width": max(x1 - x0, 1), "height": max(y1 - y0, 1)}
        with _mss_lib.mss() as sct:
            shot = sct.grab(monitor)
            return np.frombuffer(shot.raw, dtype=np.uint8).reshape(
                (shot.height, shot.width, 4)
            )
    except Exception:
        return None


def _grab_printwindow(hwnd) -> np.ndarray | None:
    """Capture window via PrintWindow — works headless (no display required).

    Uses GDI off-screen DC so RDP disconnect / no monitor doesn't break capture.
    PW_RENDERFULLCONTENT (0x2) forces GPU content (DWM) to be included.
    """
    if sys.platform != "win32":
        return None
    try:
        import win32gui
        import win32ui
        import win32con
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            return None
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfc_dc, w, h)
        save_dc.SelectObject(bmp)
        # PW_RENDERFULLCONTENT = 0x2 captures GPU-composited content
        result = ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0x2)
        if not result:
            # Fallback without PW_RENDERFULLCONTENT
            ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0)
        bmp_info = bmp.GetInfo()
        bmp_str = bmp.GetBitmapBits(True)
        arr = np.frombuffer(bmp_str, dtype=np.uint8).reshape(
            (bmp_info['bmHeight'], bmp_info['bmWidth'], 4)
        )
        win32gui.DeleteObject(bmp.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
        return arr
    except Exception:
        _log(f"[printwindow] failed hwnd={hwnd}: {traceback.format_exc()[:400]}")
        return None


def capture_loop(state: CaptureState, frame_queue: FrameQueue):
    global _BLACK_FRAME, _LOCKED_FRAME
    _BLACK_FRAME = _make_black_frame()
    _LOCKED_FRAME = _make_locked_frame()
    current_desktop = "Default"
    _capture_err_logged = False
    _capture_ok_logged = False

    _ensure_capture_libs()
    _log("[capture_loop] started")

    # Switch to Default desktop immediately — BitBlt fails under SYSTEM without this
    _switch_thread_desktop("Default")
    _log("[capture_loop] SetThreadDesktop(Default) called")

    # Create dxcam camera once — reused across frames (GPU resource)
    camera = None
    if _dxcam_available:
        try:
            import dxcam as _dxcam_mod
            camera = _dxcam_mod.create(output_color="BGR")
            _log("[capture_loop] dxcam camera created")
        except Exception:
            _log(f"[capture_loop] dxcam camera create failed: {traceback.format_exc()[:300]}")
            camera = None

    while state.running:
        desktop = state.desktop

        if desktop != current_desktop:
            _switch_thread_desktop(desktop)
            current_desktop = desktop
            _capture_ok_logged = False  # re-log after desktop switch
            _log(f"[capture_loop] desktop switched to {desktop}")
            # Reinitialize dxcam after desktop switch (GPU context changed)
            if camera is not None and desktop == "Default":
                try:
                    camera.release()
                except Exception:
                    pass
                try:
                    import dxcam as _dxcam_mod
                    camera = _dxcam_mod.create(output_color="BGR")
                except Exception:
                    camera = None

        if desktop == "Winlogon":
            # Winlogon desktop is security-isolated — BitBlt blocked even from user session.
            # Show a static "Screen Locked" frame instead of blank/reconnect.
            frame_queue.put(_LOCKED_FRAME)
            time.sleep(1 / 5)  # 5fps is enough for a static frame
            continue

        hwnd = state.active_hwnd
        if hwnd is None:
            # No window selected — stream full primary monitor
            arr = None
            # dxcam works headless (GPU compositor level)
            if camera is not None:
                try:
                    arr = camera.grab()  # full screen, no region
                except Exception:
                    arr = None
            # mss fallback — requires active display, fails headless
            if arr is None:
                try:
                    with _mss_lib.mss() as sct:
                        monitor = sct.monitors[1]
                        shot = sct.grab(monitor)
                        arr = np.frombuffer(shot.raw, dtype=np.uint8).reshape(
                            (shot.height, shot.width, 4)
                        )
                except Exception:
                    arr = None
            if arr is not None:
                frame_queue.put(_encode_frame(arr, state.quality))
            else:
                frame_queue.put(_BLACK_FRAME)
            time.sleep(1 / 30)
            continue

        if not is_window_alive(hwnd):
            state.set_hwnd(None)
            frame_queue.put(_BLACK_FRAME)
            time.sleep(0.05)
            continue

        try:
            rect = get_window_rect(hwnd)  # (x0, y0, x1, y1)
            arr = None
            _method = None

            # Primary: PrintWindow — works headless, no display/RDP required
            arr = _grab_printwindow(hwnd)
            if arr is not None:
                _method = "PrintWindow"

            # Secondary: DXGI — GPU-side (unavailable under SYSTEM but try anyway)
            if arr is None and camera is not None:
                arr = _grab_dxgi(camera, rect)
                if arr is not None:
                    _method = "dxcam"

            # Fallback: mss/GDI — requires active display session
            if arr is None:
                arr = _grab_mss(rect)
                if arr is not None:
                    _method = "mss"

            if arr is None:
                if not _capture_err_logged:
                    _log(f"[capture_loop] all methods failed hwnd={hwnd}")
                    _capture_err_logged = True
                frame_queue.put(_BLACK_FRAME)
            else:
                if not _capture_ok_logged:
                    _log(f"[capture_loop] capture OK via {_method} hwnd={hwnd}")
                    _capture_ok_logged = True
                _capture_err_logged = False
                jpeg_bytes = _encode_frame(arr, state.quality)
                frame_queue.put(jpeg_bytes)
        except Exception:
            _log(f"[capture_loop] exception: {traceback.format_exc()[:300]}")
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
