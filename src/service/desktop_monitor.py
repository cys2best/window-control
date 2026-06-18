# src/service/desktop_monitor.py
import sys
import threading
from typing import Callable

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes

    _user32   = ctypes.windll.user32
    _wtsapi32 = ctypes.windll.wtsapi32

    _WTS_SESSION_LOCK        = 0x7
    _WTS_SESSION_UNLOCK      = 0x8
    _NOTIFY_FOR_ALL_SESSIONS = 1
    _WM_WTSSESSION_CHANGE    = 0x02B1
    _WM_QUIT                 = 0x0012

    def get_current_desktop_name() -> str:
        try:
            hdesk = _user32.OpenInputDesktop(0, False, 0x0200)
            if not hdesk:
                return "Default"
            buf = ctypes.create_unicode_buffer(256)
            _user32.GetUserObjectInformationW(hdesk, 2, buf, ctypes.sizeof(buf), None)
            _user32.CloseDesktop(hdesk)
            return buf.value or "Default"
        except Exception:
            return "Default"

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

    class DesktopMonitor:
        """Watches for WTS lock/unlock events via a hidden message-only window."""

        def __init__(self, on_lock: Callable[[], None], on_unlock: Callable[[], None]):
            self._on_lock = on_lock
            self._on_unlock = on_unlock
            self._thread: threading.Thread | None = None
            self._hwnd: int = 0

        def start(self):
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

        def stop(self):
            if self._hwnd:
                _user32.PostMessageW(self._hwnd, _WM_QUIT, 0, 0)

        def _run(self):
            WndProcType = ctypes.WINFUNCTYPE(
                ctypes.c_long,
                ctypes.wintypes.HWND,
                ctypes.wintypes.UINT,
                ctypes.wintypes.WPARAM,
                ctypes.wintypes.LPARAM,
            )

            def _wnd_proc(hwnd, msg, wparam, lparam):
                if msg == _WM_WTSSESSION_CHANGE:
                    _WTS_NAMES = {
                        1: "CONSOLE_CONNECT", 2: "CONSOLE_DISCONNECT",
                        3: "REMOTE_CONNECT",  4: "REMOTE_DISCONNECT",
                        5: "SESSION_LOGON",   6: "SESSION_LOGOFF",
                        7: "SESSION_LOCK",    8: "SESSION_UNLOCK",
                        9: "SESSION_REMOTE_CONTROL",
                    }
                    _log(f"[WTS] event={wparam} ({_WTS_NAMES.get(wparam,'?')}) session={lparam}")
                    if wparam == _WTS_SESSION_LOCK:
                        self._on_lock()
                    elif wparam == _WTS_SESSION_UNLOCK:
                        self._on_unlock()
                    return 0
                return _user32.DefWindowProcW(hwnd, msg, wparam, lparam)

            _proc = WndProcType(_wnd_proc)

            class WNDCLASSW(ctypes.Structure):
                _fields_ = [
                    ("style",         ctypes.wintypes.UINT),
                    ("lpfnWndProc",   ctypes.c_void_p),
                    ("cbClsExtra",    ctypes.c_int),
                    ("cbWndExtra",    ctypes.c_int),
                    ("hInstance",     ctypes.wintypes.HANDLE),
                    ("hIcon",         ctypes.wintypes.HANDLE),
                    ("hCursor",       ctypes.wintypes.HANDLE),
                    ("hbrBackground", ctypes.wintypes.HANDLE),
                    ("lpszMenuName",  ctypes.c_wchar_p),
                    ("lpszClassName", ctypes.c_wchar_p),
                ]

            wc = WNDCLASSW()
            wc.lpfnWndProc = ctypes.cast(_proc, ctypes.c_void_p)
            wc.lpszClassName = "WCDesktopMonitor"
            _user32.RegisterClassW(ctypes.byref(wc))

            _user32.CreateWindowExW.restype = ctypes.wintypes.HWND
            self._hwnd = _user32.CreateWindowExW(
                0, "WCDesktopMonitor", "", 0,
                0, 0, 0, 0, None, None, None, None
            )

            _wtsapi32.WTSRegisterSessionNotification(self._hwnd, _NOTIFY_FOR_ALL_SESSIONS)

            # Message loop
            class MSG(ctypes.Structure):
                _fields_ = [
                    ("hwnd",    ctypes.wintypes.HWND),
                    ("message", ctypes.wintypes.UINT),
                    ("wParam",  ctypes.wintypes.WPARAM),
                    ("lParam",  ctypes.wintypes.LPARAM),
                    ("time",    ctypes.wintypes.DWORD),
                    ("pt",      ctypes.wintypes.POINT),
                ]

            msg = MSG()
            while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                _user32.TranslateMessage(ctypes.byref(msg))
                _user32.DispatchMessageW(ctypes.byref(msg))

            _wtsapi32.WTSUnRegisterSessionNotification(self._hwnd)

else:
    def get_current_desktop_name() -> str:
        return "Default"

    class DesktopMonitor:
        def __init__(self, on_lock, on_unlock):
            pass
        def start(self):
            pass
        def stop(self):
            pass
