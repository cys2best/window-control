import sys
import ctypes
import numpy as np
from PIL import Image
import io

from server.window_manager import get_window_rect

PREVIEW_WIDTH = 200
PREVIEW_HEIGHT = 120


def _dark_placeholder() -> bytes:
    img = Image.new("RGB", (PREVIEW_WIDTH, PREVIEW_HEIGHT), color=(30, 30, 30))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=60)
    return buf.getvalue()


def _grab_printwindow(hwnd) -> np.ndarray | None:
    if sys.platform != "win32":
        return None
    try:
        import win32gui, win32ui
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
        ctypes.windll.user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), 0x2)
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
        return None


def capture_preview(hwnd) -> bytes:
    try:
        arr = _grab_printwindow(hwnd)
        if arr is None:
            # mss fallback (works when RDP connected)
            if sys.platform == "win32":
                import mss as mss_lib
            else:
                from stubs import mss_stub as mss_lib
            rect = get_window_rect(hwnd)
            x0, y0, x1, y1 = rect
            monitor = {"left": x0, "top": y0, "width": max(x1-x0,1), "height": max(y1-y0,1)}
            with mss_lib.mss() as sct:
                shot = sct.grab(monitor)
                arr = np.frombuffer(shot.raw, dtype=np.uint8).reshape((shot.height, shot.width, 4))
        if arr is None:
            return _dark_placeholder()
        img = Image.fromarray(arr[:, :, 2::-1], "RGB")
        img.thumbnail((PREVIEW_WIDTH, PREVIEW_HEIGHT), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=60)
        return buf.getvalue()
    except Exception:
        return _dark_placeholder()
