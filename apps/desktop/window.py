# apps/desktop/window.py
import webview


class DesktopWindow:
    def __init__(self, url: str):
        self._url = url
        self._window = None

    def show(self):
        if self._window is not None:
            self._window.show()
            return
        self._window = webview.create_window("WindowControl", self._url, width=1100, height=750)

    def start(self):
        webview.start()
