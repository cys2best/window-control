# apps/desktop/webview_main.py
"""Standalone entry point for the pywebview desktop shell.

This module exists to be run as a *process of its own* (`python
webview_main.py <url>`, or the frozen app re-invoked with
`--webview-window <url>` -- see apps/desktop/window.py). That is not an
aesthetic choice: pywebview's `webview.start()` begins with

    if threading.current_thread().name != 'MainThread':
        raise WebViewException('pywebview must be run on a main thread.')

so it can only ever run on a process's real main thread. In the main
WindowControl process that thread is already owned, for the whole life of
the app, by PyQt5's `QApplication.exec_()` (tray + launcher), and PyQt5
will not give it back. Running `webview.start()` on a background thread
there does not work around the constraint -- it raises, every time,
inside a daemon thread where nothing surfaces the error.

Giving the webview loop its own process gives it its own free main
thread. Nothing else lives here: no server, no tray, no engine.
"""
import sys

TITLE = "WindowControl"
WIDTH = 1100
HEIGHT = 750


def run(url: str) -> None:
    """Open the shell window and block until the user closes it.

    `webview` is imported here rather than at module scope so that
    importing this module (which src/main.py does to dispatch
    `--webview-window`) doesn't drag pywebview's GUI stack into the
    parent process that will never call it.
    """
    import webview

    webview.create_window(TITLE, url, width=WIDTH, height=HEIGHT)
    webview.start()


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or not args[0]:
        print("usage: webview_main.py <url>", file=sys.stderr)
        return 2
    run(args[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
