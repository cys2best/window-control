import sys

if sys.platform == "win32":
    import win32api
    import win32con
    import win32gui
else:
    from stubs import win32_stub as win32api
    from stubs import win32_stub as win32con
    from stubs import win32_stub as win32gui

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
}


def _client_coords(hwnd, nx: float, ny: float) -> tuple[int, int]:
    """Convert normalized (0-1) stream coords to window client-area pixel coords."""
    rect = win32gui.GetWindowRect(hwnd)
    x0, y0, x1, y1 = rect
    w, h = x1 - x0, y1 - y0
    # Absolute screen position of the click
    ax, ay = int(x0 + nx * w), int(y0 + ny * h)
    # Convert to client coords (what WM_LBUTTONDOWN expects)
    cx, cy = win32gui.ScreenToClient(hwnd, (ax, ay))
    return cx, cy


def make_lparam(x: int, y: int) -> int:
    return ((y & 0xFFFF) << 16) | (x & 0xFFFF)


def handle_click(hwnd, nx: float, ny: float):
    cx, cy = _client_coords(hwnd, nx, ny)
    lp = make_lparam(cx, cy)
    win32api.PostMessage(hwnd, 0x0201, 0x0001, lp)  # WM_LBUTTONDOWN
    win32api.PostMessage(hwnd, 0x0202, 0, lp)       # WM_LBUTTONUP


def handle_move(hwnd, nx: float, ny: float):
    cx, cy = _client_coords(hwnd, nx, ny)
    lp = make_lparam(cx, cy)
    win32api.PostMessage(hwnd, 0x0200, 0, lp)  # WM_MOUSEMOVE


def handle_scroll(hwnd, dx: int, dy: int):
    delta = dy * 120  # WHEEL_DELTA
    win32api.PostMessage(hwnd, 0x020A, (delta << 16), 0)  # WM_MOUSEWHEEL


def handle_key(hwnd, key: str):
    vk = KEY_MAP.get(key)
    if vk is None and len(key) == 1:
        vk = ord(key.upper())
    if vk is None:
        return
    win32api.PostMessage(hwnd, 0x0100, vk, 0)  # WM_KEYDOWN
    win32api.PostMessage(hwnd, 0x0101, vk, 0)  # WM_KEYUP
