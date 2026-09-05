# apps/desktop/window.py
"""Desktop shell: apps/web's served build in a native pywebview window.

`DesktopWindow` does not call pywebview in this process at all. It
launches `webview_main.py` as a child process instead, because
`webview.start()` refuses to run anywhere but a process's real main
thread (it raises `WebViewException('pywebview must be run on a main
thread.')` before doing anything else), and this process's main thread
belongs to PyQt5's `QApplication.exec_()` for the whole life of the app.
An earlier version started `webview.start()` on a background daemon
thread; that raised on every single "Open App" click, invisibly, and no
window ever opened. See webview_main.py's own docstring.
"""
import os
import subprocess
import sys

# Flag the frozen app dispatches on to re-enter itself as a webview host
# (src/main.py's main()). In a frozen build `sys.executable` is
# WindowControl.exe, not a Python interpreter, so there is no separate
# script path to hand it -- the app re-invokes itself instead.
WEBVIEW_ARG = "--webview-window"


def _entry_script() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "webview_main.py")


def _no_window_flags() -> dict:
    """Suppress the console flash on Windows (same pattern as adb_manager)."""
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}  # CREATE_NO_WINDOW
    return {}


class DesktopWindow:
    def __init__(self, url: str, popen=None):
        self._url = url
        # Injectable purely so tests can observe the launch without
        # spawning a real GUI process.
        self._popen = popen or subprocess.Popen
        self._process = None

    def command(self) -> list:
        if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
            return [sys.executable, WEBVIEW_ARG, self._url]
        return [sys.executable, _entry_script(), self._url]

    def show(self) -> None:
        """Open the window, or do nothing if it is already open.

        One window, not one per click -- the same guarantee the previous
        in-process implementation gave by caching pywebview's window
        handle, expressed here as "don't spawn a second child while the
        first is still alive". Once the user closes the window (child
        exits), a later click opens a fresh one.
        """
        if self._process is not None and self._process.poll() is None:
            return
        self._process = self._popen(self.command(), **_no_window_flags())

    def close(self) -> None:
        """Terminate the shell window's process, if one is running."""
        process, self._process = self._process, None
        if process is None or process.poll() is not None:
            return
        try:
            process.terminate()
        except Exception:
            pass
