import os
import sys

PORT = 8080
DEV_MODE = sys.platform != "win32"
VERSION = "2.3.3"
GITHUB_REPO = "cys2best/window-control"

QUALITY_MAP = {
    "low": 40,
    "medium": 65,
    "high": 85,
}
DEFAULT_QUALITY = "high"
assert DEFAULT_QUALITY in QUALITY_MAP, f"DEFAULT_QUALITY '{DEFAULT_QUALITY}' not in QUALITY_MAP"

TIER_ORDER = ["480", "720", "1080", "1440"]
DEFAULT_TIER = "720"
QUALITY_TIERS = {
    "480":  {"max_size": 480,  "bit_rate": "2M",  "max_fps": 30},
    "720":  {"max_size": 720,  "bit_rate": "4M",  "max_fps": 30},
    "1080": {"max_size": 1080, "bit_rate": "8M",  "max_fps": 60},
    "1440": {"max_size": 1440, "bit_rate": "12M", "max_fps": 60},
}
assert DEFAULT_TIER in QUALITY_TIERS
assert set(TIER_ORDER) == set(QUALITY_TIERS)

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

