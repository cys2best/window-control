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
    _mss_lib = None
    _dxcam_available = None  # None = not yet checked
    _jpeg = None

    def _ensure_capture_libs():
        global _mss_lib, _dxcam_available, _jpeg
        if _mss_lib is not None:
            return
        import mss as mss_lib
        _mss_lib = mss_lib
        try:
            import dxcam
            _dxcam_available = True
        except Exception:
            _dxcam_available = False
        try:
            from turbojpeg import TurboJPEG
            _jpeg = TurboJPEG()
        except Exception:
            _jpeg = None
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


def capture_loop(state: CaptureState, frame_queue: FrameQueue):
    global _BLACK_FRAME
    _BLACK_FRAME = _make_black_frame()
    current_desktop = "Default"

    _ensure_capture_libs()

    # Create dxcam camera once — reused across frames (GPU resource)
    camera = None
    if _dxcam_available:
        try:
            import dxcam as _dxcam_mod
            camera = _dxcam_mod.create(output_color="BGR")
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
                    import dxcam as _dxcam_mod
                    camera = _dxcam_mod.create(output_color="BGR")
                except Exception:
                    camera = None

        if desktop == "Winlogon":
            # Lock screen: DXGI is blocked from Winlogon desktop.
            # Use mss/GDI (SetThreadDesktop already switched above).
            try:
                with _mss_lib.mss() as sct:
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
