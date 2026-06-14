import sys
from dataclasses import dataclass

if sys.platform == "win32":
    import win32gui
    import win32process
    import win32con
else:
    from stubs import win32_stub as win32gui
    win32process = win32gui
    win32con = win32gui

from config import SYSTEM_WINDOW_TITLES


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    icon_b64: str = ""


def is_system_window(title: str) -> bool:
    return title in SYSTEM_WINDOW_TITLES


def _enum_callback(hwnd, result):
    if not win32gui.IsWindowVisible(hwnd):
        return
    title = win32gui.GetWindowText(hwnd)
    if is_system_window(title):
        return
    rect = win32gui.GetWindowRect(hwnd)
    if rect[2] - rect[0] <= 0 or rect[3] - rect[1] <= 0:
        return
    icon_b64 = _get_icon_b64(hwnd)
    result.append(WindowInfo(hwnd=hwnd, title=title, icon_b64=icon_b64))


def _get_icon_b64(hwnd) -> str:
    try:
        hicon = win32gui.GetClassLong(hwnd, win32gui.GCL_HICONSM)
        if not hicon:
            return ""
        # On Windows: extract icon via win32ui/win32con; stub returns 0 so we skip
        return ""
    except Exception:
        return ""


def list_windows() -> list[WindowInfo]:
    result = []
    win32gui.EnumWindows(_enum_callback, result)
    return result


def get_window_rect(hwnd) -> tuple[int, int, int, int]:
    return win32gui.GetWindowRect(hwnd)


def is_window_alive(hwnd) -> bool:
    return bool(win32gui.IsWindow(hwnd)) and not bool(win32gui.IsIconic(hwnd))


def focus_window(hwnd) -> None:
    """Restore minimized window and bring it to foreground via AttachThreadInput."""
    import time
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.05)
        fg = win32gui.GetForegroundWindow()
        if fg == hwnd:
            return
        if sys.platform == "win32":
            import ctypes
            fg_tid, _ = win32process.GetWindowThreadProcessId(fg)
            tgt_tid, _ = win32process.GetWindowThreadProcessId(hwnd)
            if fg_tid != tgt_tid:
                ctypes.windll.user32.AttachThreadInput(fg_tid, tgt_tid, True)
                win32gui.SetForegroundWindow(hwnd)
                win32gui.BringWindowToTop(hwnd)
                ctypes.windll.user32.AttachThreadInput(fg_tid, tgt_tid, False)
            else:
                win32gui.SetForegroundWindow(hwnd)
                win32gui.BringWindowToTop(hwnd)
    except Exception:
        pass
