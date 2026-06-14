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
