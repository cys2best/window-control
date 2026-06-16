# src/service/auto_unlock.py
import sys
import time
import threading

import os
import ctypes
import ctypes.wintypes

_PASSWORD_FILE = r"C:\ProgramData\WindowControl\unlock.dat"


def _dpapi_encrypt(data: bytes) -> bytes:
    """Encrypt with DPAPI LOCAL_MACHINE scope — readable by SYSTEM and user."""
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data)
    inp = DATA_BLOB(len(data), buf)
    out = DATA_BLOB()
    # CRYPTPROTECT_LOCAL_MACHINE = 0x4 — any process on this machine can decrypt
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(inp), None, None, None, None, 0x4, ctypes.byref(out)
    )
    if not ok:
        raise RuntimeError(f"CryptProtectData failed: {ctypes.GetLastError()}")
    result = bytes(out.pbData[:out.cbData])
    ctypes.windll.kernel32.LocalFree(out.pbData)
    return result


def _dpapi_decrypt(data: bytes) -> bytes:
    """Decrypt DPAPI blob."""
    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data)
    inp = DATA_BLOB(len(data), buf)
    out = DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(inp), None, None, None, None, 0x4, ctypes.byref(out)
    )
    if not ok:
        raise RuntimeError(f"CryptUnprotectData failed: {ctypes.GetLastError()}")
    result = bytes(out.pbData[:out.cbData])
    ctypes.windll.kernel32.LocalFree(out.pbData)
    return result


def store_password(password: str) -> None:
    if sys.platform != "win32":
        return
    os.makedirs(os.path.dirname(_PASSWORD_FILE), exist_ok=True)
    encrypted = _dpapi_encrypt(password.encode("utf-8"))
    with open(_PASSWORD_FILE, "wb") as f:
        f.write(encrypted)


def get_stored_password() -> str | None:
    if sys.platform != "win32":
        return None
    try:
        with open(_PASSWORD_FILE, "rb") as f:
            encrypted = f.read()
        return _dpapi_decrypt(encrypted).decode("utf-8")
    except Exception:
        return None


def delete_password() -> None:
    try:
        os.remove(_PASSWORD_FILE)
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


def _log(msg: str):
    import os
    for _p in [r"C:\ProgramData\WindowControl", r"C:\Windows\Temp"]:
        try:
            os.makedirs(_p, exist_ok=True)
            with open(os.path.join(_p, "service_crash.log"), "a") as f:
                f.write(msg + "\n")
            return
        except Exception:
            continue


def auto_unlock_on_lock():
    """
    Called when WTS_SESSION_LOCK fires.
    Waits for Winlogon to render, then types stored password.
    Run in a daemon thread — do not block the caller.
    """
    try:
        password = get_stored_password()
    except Exception as e:
        _log(f"[auto_unlock] get_stored_password failed: {e}")
        password = None

    if not password:
        _log("[auto_unlock] no password stored — skipping auto-unlock")
        return

    _log(f"[auto_unlock] password found (len={len(password)}), scheduling unlock in 1.5s")

    def _run():
        time.sleep(1.5)  # wait for Winlogon desktop to fully render
        _log("[auto_unlock] typing password to Winlogon")
        try:
            _type_password_to_winlogon(password)
            _log("[auto_unlock] done")
        except Exception as e:
            _log(f"[auto_unlock] _type_password_to_winlogon failed: {e}")

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
