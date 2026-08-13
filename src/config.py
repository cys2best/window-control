import os
import sys

PORT = 8080
DEV_MODE = sys.platform != "win32"
VERSION = "2.1.4"
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

# mediamtx / scrcpy
MEDIAMTX_PORT = 8554   # RTSP
WHEP_PORT = 8889       # WebRTC/WHEP (mediamtx default)
RTMP_PORT = 1935       # mediamtx RTMP (unused by us, kept for mediamtx default config)
WEBRTC_UDP_PORT = 8288 # WebRTC ICE UDP mux (mediamtx default 8000 collided)
STUN_PORT = 3478       # embedded STUN server, bound to Tailscale IP (see stun_server.py)

ADB_PATH = "adb"       # overridden at runtime by _find_adb()
SCRCPY_PATH = os.path.join("assets", "scrcpy", "scrcpy.exe")
MEDIAMTX_PATH = os.path.join("assets", "mediamtx", "mediamtx.exe")


def get_base_path():
    if hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


BASE_PATH = get_base_path()
CLIENT_DIR = os.path.join(BASE_PATH, "client")
ASSETS_DIR = os.path.join(BASE_PATH, "assets")

