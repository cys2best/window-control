# src/gui/tray.py
import sys
import threading
from pathlib import Path
from PIL import Image
import pystray

from config import ASSETS_DIR


def _load_tray_icon() -> Image.Image:
    icon_path = Path(ASSETS_DIR) / "tray_icon.png"
    if icon_path.exists():
        return Image.open(icon_path)
    # Fallback: solid blue 64x64 square
    return Image.new("RGB", (64, 64), color=(30, 120, 200))


class TrayIcon:
    def __init__(self, on_show, on_stop_server, on_exit, on_reinstall=None):
        """
        on_show: callable() — show/raise the launcher window
        on_stop_server: callable() — stop the streaming server
        on_exit: callable() — quit the entire application
        on_reinstall: callable() | None — force download and install latest release
        """
        self._on_show = on_show
        self._on_stop_server = on_stop_server
        self._on_exit = on_exit
        self._on_reinstall = on_reinstall
        self._icon = None

    def _build_menu(self):
        items = [
            pystray.MenuItem("Show", self._handle_show, default=True),
            pystray.MenuItem("Stop Server", self._handle_stop),
            pystray.Menu.SEPARATOR,
        ]
        if self._on_reinstall is not None:
            items.append(pystray.MenuItem("Reinstall / Update", self._handle_reinstall))
        items.append(pystray.MenuItem("Exit", self._handle_exit))
        return pystray.Menu(*items)

    def _handle_show(self, icon, item):
        self._on_show()

    def _handle_stop(self, icon, item):
        self._on_stop_server()

    def _handle_reinstall(self, icon, item):
        if self._on_reinstall:
            self._on_reinstall()

    def _handle_exit(self, icon, item):
        self._on_exit()
        icon.stop()

    def start(self):
        """Run tray icon in a background daemon thread."""
        img = _load_tray_icon()
        self._icon = pystray.Icon(
            "WindowControl",
            img,
            "WindowControl",
            menu=self._build_menu(),
        )
        t = threading.Thread(target=self._icon.run, daemon=True)
        t.start()

    def notify(self, message: str, title: str = "WindowControl"):
        if self._icon:
            self._icon.notify(message, title)

    def stop(self):
        if self._icon:
            self._icon.stop()
