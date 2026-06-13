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
    def __init__(self, on_show, on_stop_server, on_exit):
        """
        on_show: callable() — show/raise the launcher window
        on_stop_server: callable() — stop the streaming server
        on_exit: callable() — quit the entire application
        """
        self._on_show = on_show
        self._on_stop_server = on_stop_server
        self._on_exit = on_exit
        self._icon = None

    def _build_menu(self):
        return pystray.Menu(
            pystray.MenuItem("Show", self._handle_show, default=True),
            pystray.MenuItem("Stop Server", self._handle_stop),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Exit", self._handle_exit),
        )

    def _handle_show(self, icon, item):
        self._on_show()

    def _handle_stop(self, icon, item):
        self._on_stop_server()

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

    def stop(self):
        if self._icon:
            self._icon.stop()
