import sys
import numpy as np
from PIL import Image
import io

if sys.platform == "win32":
    import mss as mss_lib
else:
    from stubs import mss_stub as mss_lib

from server.window_manager import get_window_rect


PREVIEW_WIDTH = 200
PREVIEW_HEIGHT = 120


def capture_preview(hwnd) -> bytes:
    """Return JPEG bytes of a thumbnail for the given window handle."""
    try:
        rect = get_window_rect(hwnd)
        x0, y0, x1, y1 = rect
        w, h = max(x1 - x0, 1), max(y1 - y0, 1)
        monitor = {"left": x0, "top": y0, "width": w, "height": h}
        with mss_lib.mss() as sct:
            shot = sct.grab(monitor)
            arr = np.frombuffer(shot.raw, dtype=np.uint8).reshape((shot.height, shot.width, 4))
        img = Image.fromarray(arr[:, :, :3], "RGB")
        img.thumbnail((PREVIEW_WIDTH, PREVIEW_HEIGHT), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=60)
        return buf.getvalue()
    except Exception:
        img = Image.new("RGB", (PREVIEW_WIDTH, PREVIEW_HEIGHT), color=(30, 30, 30))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=60)
        return buf.getvalue()
