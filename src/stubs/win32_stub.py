import os

GCL_HICONSM = -14
WM_LBUTTONDOWN = 0x0201
WM_LBUTTONUP = 0x0202
WM_RBUTTONDOWN = 0x0204
WM_RBUTTONUP = 0x0205
WM_MOUSEMOVE = 0x0200
WM_MOUSEWHEEL = 0x020A
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
MK_LBUTTON = 0x0001

_STUB_WINDOWS = [
    (1001, "Chrome — Google Chrome"),
    (1002, "VS Code"),
    (1003, "Notepad"),
]


def EnumWindows(callback, extra):
    for hwnd, title in _STUB_WINDOWS:
        callback(hwnd, extra)


def GetWindowText(hwnd):
    for h, title in _STUB_WINDOWS:
        if h == hwnd:
            return title
    return ""


def IsWindowVisible(hwnd):
    return hwnd in {h for h, _ in _STUB_WINDOWS}


def GetWindowRect(hwnd):
    return (0, 0, 1280, 720)


def IsWindow(hwnd):
    return hwnd in {h for h, _ in _STUB_WINDOWS}


def IsIconic(hwnd):
    return False


def GetClassLong(hwnd, index):
    return 0


def GetWindowThreadProcessId(hwnd, pid_ptr):
    return 0


def PostMessage(hwnd, msg, wparam, lparam):
    pass


def SendMessage(hwnd, msg, wparam, lparam):
    return 0


def SetForegroundWindow(hwnd):
    pass
