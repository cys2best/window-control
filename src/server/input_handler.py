import sys
import time

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes
    import win32api
    import win32con
    import win32gui
    import win32process
else:
    from stubs import win32_stub as win32api
    from stubs import win32_stub as win32con
    from stubs import win32_stub as win32gui
    win32process = win32gui
    ctypes = None

KEY_MAP = {
    "Return": 0x0D,
    "BackSpace": 0x08,
    "Tab": 0x09,
    "Escape": 0x1B,
    "Delete": 0x2E,
    "ArrowLeft": 0x25,
    "ArrowUp": 0x26,
    "ArrowRight": 0x27,
    "ArrowDown": 0x28,
    "F1": 0x70, "F2": 0x71, "F3": 0x72, "F4": 0x73,
    "F5": 0x74, "F6": 0x75, "F7": 0x76, "F8": 0x77,
    "F9": 0x78, "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
    " ": 0x20, "Space": 0x20,
}

# SendInput structures
if sys.platform == "win32":
    MOUSEEVENTF_MOVE        = 0x0001
    MOUSEEVENTF_LEFTDOWN    = 0x0002
    MOUSEEVENTF_LEFTUP      = 0x0004
    MOUSEEVENTF_RIGHTDOWN   = 0x0008
    MOUSEEVENTF_RIGHTUP     = 0x0010
    MOUSEEVENTF_WHEEL       = 0x0800
    MOUSEEVENTF_ABSOLUTE    = 0x8000

    KEYEVENTF_KEYUP         = 0x0002

    INPUT_MOUSE    = 0
    INPUT_KEYBOARD = 1

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx",          ctypes.c_long),
            ("dy",          ctypes.c_long),
            ("mouseData",   ctypes.c_ulong),
            ("dwFlags",     ctypes.c_ulong),
            ("time",        ctypes.c_ulong),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk",         ctypes.c_ushort),
            ("wScan",       ctypes.c_ushort),
            ("dwFlags",     ctypes.c_ulong),
            ("time",        ctypes.c_ulong),
            ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong)),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ulong), ("_input", _INPUT_UNION)]

    _user32 = ctypes.windll.user32
    _SM_CXSCREEN = 0
    _SM_CYSCREEN = 1


def _screen_size():
    return (
        ctypes.windll.user32.GetSystemMetrics(_SM_CXSCREEN),
        ctypes.windll.user32.GetSystemMetrics(_SM_CYSCREEN),
    )


def _abs_coords(hwnd, nx: float, ny: float) -> tuple[int, int]:
    """Normalized (0-1) → absolute screen pixel coords inside window."""
    x0, y0, x1, y1 = win32gui.GetWindowRect(hwnd)
    w, h = x1 - x0, y1 - y0
    return int(x0 + nx * w), int(y0 + ny * h)


def _to_send_input_coords(ax: int, ay: int) -> tuple[int, int]:
    """Absolute screen px → SendInput normalized (0-65535)."""
    sw, sh = _screen_size()
    return int(ax * 65535 / (sw - 1)), int(ay * 65535 / (sh - 1))


def _focus_window(hwnd):
    """Bring hwnd to foreground using AttachThreadInput to bypass Windows restriction."""
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(0.05)

        fg = win32gui.GetForegroundWindow()
        if fg == hwnd:
            return

        fg_tid, _ = win32process.GetWindowThreadProcessId(fg)
        our_tid, _ = win32process.GetWindowThreadProcessId(hwnd)

        attached = False
        if fg_tid != our_tid:
            attached = ctypes.windll.user32.AttachThreadInput(fg_tid, our_tid, True)

        win32gui.SetForegroundWindow(hwnd)
        win32gui.BringWindowToTop(hwnd)

        if attached:
            ctypes.windll.user32.AttachThreadInput(fg_tid, our_tid, False)
    except Exception:
        pass


def _send_mouse(flags: int, ax: int = 0, ay: int = 0, data: int = 0):
    sx, sy = _to_send_input_coords(ax, ay)
    mi = MOUSEINPUT(
        dx=sx, dy=sy,
        mouseData=data,
        dwFlags=flags | MOUSEEVENTF_ABSOLUTE,
        time=0,
        dwExtraInfo=None,
    )
    inp = INPUT(type=INPUT_MOUSE, _input=_INPUT_UNION(mi=mi))
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def _send_key(vk: int, key_up: bool = False):
    ki = KEYBDINPUT(
        wVk=vk,
        wScan=0,
        dwFlags=KEYEVENTF_KEYUP if key_up else 0,
        time=0,
        dwExtraInfo=None,
    )
    inp = INPUT(type=INPUT_KEYBOARD, _input=_INPUT_UNION(ki=ki))
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(inp))


def handle_click(hwnd, nx: float, ny: float):
    _focus_window(hwnd)
    ax, ay = _abs_coords(hwnd, nx, ny)
    _send_mouse(MOUSEEVENTF_MOVE, ax, ay)
    time.sleep(0.02)
    _send_mouse(MOUSEEVENTF_LEFTDOWN, ax, ay)
    time.sleep(0.02)
    _send_mouse(MOUSEEVENTF_LEFTUP, ax, ay)


def handle_move(hwnd, nx: float, ny: float):
    ax, ay = _abs_coords(hwnd, nx, ny)
    _send_mouse(MOUSEEVENTF_MOVE, ax, ay)


def handle_scroll(hwnd, dx: int, dy: int):
    ax, ay = _abs_coords(hwnd, 0.5, 0.5)
    delta = dy * 120
    _send_mouse(MOUSEEVENTF_WHEEL, ax, ay, data=delta & 0xFFFFFFFF)


def handle_key(hwnd, key: str):
    _focus_window(hwnd)
    vk = KEY_MAP.get(key)
    if vk is None and len(key) == 1:
        vk = ord(key.upper())
    if vk is None:
        return
    _send_key(vk, key_up=False)
    time.sleep(0.02)
    _send_key(vk, key_up=True)


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
