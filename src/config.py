import os
import sys

PORT = 8080
DEV_MODE = sys.platform != "win32"

QUALITY_MAP = {
    "low": 40,
    "medium": 65,
    "high": 85,
}
DEFAULT_QUALITY = "high"

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
