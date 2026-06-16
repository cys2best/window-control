# src/service/desktop_monitor.py
import sys
import threading
from typing import Callable

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes
    import win32api
    import win32con
    import win32gui
    import win32ts
    import win32security

    _WTS_SESSION_LOCK   = 0x7
    _WTS_SESSION_UNLOCK = 0x8
    _NOTIFY_FOR_ALL_SESSIONS = 1

    def get_current_desktop_name() -> str:
        try:
            hdesk = ctypes.windll.user32.OpenInputDesktop(0, False, 0x0200)
            if not hdesk:
                return "Default"
            buf = ctypes.create_unicode_buffer(256)
            ctypes.windll.user32.GetUserObjectInformationW(
                hdesk, 2, buf, ctypes.sizeof(buf), None
            )
            ctypes.windll.user32.CloseDesktop(hdesk)
            return buf.value or "Default"
        except Exception:
            return "Default"

    class DesktopMonitor:
        """Watches for WTS lock/unlock events. Runs its own message loop thread."""

        def __init__(
            self,
            on_lock: Callable[[], None],
            on_unlock: Callable[[], None],
        ):
            self._on_lock = on_lock
            self._on_unlock = on_unlock
            self._thread: threading.Thread | None = None
            self._hwnd = None

        def start(self):
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()

        def stop(self):
            if self._hwnd:
                try:
                    win32gui.PostMessage(self._hwnd, win32con.WM_QUIT, 0, 0)
                except Exception:
                    pass

        def _run(self):
            wc = win32gui.WNDCLASS()
            wc.lpszClassName = "WCDesktopMonitor"
            wc.lpfnWndProc = self._wnd_proc
            win32gui.RegisterClass(wc)
            self._hwnd = win32gui.CreateWindow(
                "WCDesktopMonitor", "", 0, 0, 0, 0, 0, 0, 0, None, None
            )
            # Register for WTS session notifications
            win32ts.WTSRegisterSessionNotification(
                self._hwnd, _NOTIFY_FOR_ALL_SESSIONS
            )
            win32gui.PumpMessages()
            win32ts.WTSUnRegisterSessionNotification(self._hwnd)

        def _wnd_proc(self, hwnd, msg, wparam, lparam):
            _WM_WTSSESSION_CHANGE = 0x02B1
            if msg == _WM_WTSSESSION_CHANGE:
                _WTS_NAMES = {
                    1: "CONSOLE_CONNECT", 2: "CONSOLE_DISCONNECT",
                    3: "REMOTE_CONNECT",  4: "REMOTE_DISCONNECT",
                    5: "SESSION_LOGON",   6: "SESSION_LOGOFF",
                    7: "SESSION_LOCK",    8: "SESSION_UNLOCK",
                    9: "SESSION_REMOTE_CONTROL",
                }
                from service.pipe_server import PIPE_NAME
                import os
                try:
                    os.makedirs(r"C:\ProgramData\WindowControl", exist_ok=True)
                    with open(r"C:\ProgramData\WindowControl\service_crash.log", "a") as f:
                        f.write(f"[WTS] event={wparam} ({_WTS_NAMES.get(wparam,'?')}) session={lparam}\n")
                except Exception:
                    pass
                if wparam == _WTS_SESSION_LOCK:
                    self._on_lock()
                elif wparam == _WTS_SESSION_UNLOCK:
                    self._on_unlock()
            return win32gui.DefWindowProc(hwnd, msg, wparam, lparam)

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
