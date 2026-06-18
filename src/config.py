import os
import sys

PORT = 8080
DEV_MODE = sys.platform != "win32"
VERSION = "1.2.28"
GITHUB_REPO = "cys2best/window-control"

QUALITY_MAP = {
    "low": 40,
    "medium": 65,
    "high": 85,
}
DEFAULT_QUALITY = "high"
assert DEFAULT_QUALITY in QUALITY_MAP, f"DEFAULT_QUALITY '{DEFAULT_QUALITY}' not in QUALITY_MAP"

SYSTEM_WINDOW_TITLES = {
    "Program Manager", "Desktop", "Taskbar",
    "Task Manager", "Start", "",
}


def get_base_path():
    if hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


BASE_PATH = get_base_path()
CLIENT_DIR = os.path.join(BASE_PATH, "client")
ASSETS_DIR = os.path.join(BASE_PATH, "assets")

