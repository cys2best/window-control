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
        from turbojpeg import TurboJPEG
        _jpeg = TurboJPEG()
    except (RuntimeError, OSError, ImportError):
        _jpeg = None
else:
    from stubs import mss_stub as mss_lib
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
        self._lock = threading.Lock()

    def set_hwnd(self, hwnd):
        with self._lock:
            self.active_hwnd = hwnd
            self.window_available = hwnd is not None

    def set_quality(self, q: int):
        with self._lock:
            self.quality = q


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


def capture_loop(state: CaptureState, frame_queue: FrameQueue):
    global _BLACK_FRAME
    _BLACK_FRAME = _make_black_frame()

    while state.running:
        hwnd = state.active_hwnd
        if hwnd is None:
            frame_queue.put(_BLACK_FRAME)
            time.sleep(0.05)
            continue

        if not is_window_alive(hwnd):
            state.set_hwnd(None)  # sets window_available=False inside lock
            frame_queue.put(_BLACK_FRAME)
            time.sleep(0.05)
            continue

        try:
            rect = get_window_rect(hwnd)
            x0, y0, x1, y1 = rect
            w, h = max(x1 - x0, 1), max(y1 - y0, 1)
            monitor = {"left": x0, "top": y0, "width": w, "height": h}
            with mss_lib.mss() as sct:
                shot = sct.grab(monitor)
                arr = np.frombuffer(shot.raw, dtype=np.uint8).reshape(
                    (shot.height, shot.width, 4)
                )
            jpeg_bytes = _encode_frame(arr, state.quality)
            frame_queue.put(jpeg_bytes)
        except Exception:
            frame_queue.put(_BLACK_FRAME)

        time.sleep(1 / 30)  # ~30 fps cap


def mjpeg_generator(frame_queue: FrameQueue):
    while True:
        frame = frame_queue.get(timeout=1.0)
        if frame is None:
            continue
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        )
