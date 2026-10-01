import os
import sys

PORT = 8080
DEV_MODE = sys.platform != "win32"
VERSION = "3.2.0"
GITHUB_REPO = "cys2best/window-control"

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

# Engine / scrcpy
STUN_PORT = 3478       # embedded STUN server, bound to Tailscale IP (see stun_server.py)
ENGINE_LOCAL_ICE_SERVERS = tuple(filter(None, os.environ.get(
    "ENGINE_LOCAL_ICE_SERVERS", ""
).split(",")))

ADB_PATH = "adb"       # overridden at runtime by _find_adb()
SCRCPY_PATH = os.path.join("assets", "scrcpy", "scrcpy.exe")


def get_base_path():
    if hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


BASE_PATH = get_base_path()
ASSETS_DIR = os.path.join(BASE_PATH, "assets")


def get_web_build_dir() -> str:
    """apps/web's Next.js static export (`npm run build -w apps/web`,
    output: "export" -> apps/web/out). In a dev checkout BASE_PATH is
    src/, a sibling of the repo-root apps/ directory, so the build output
    is reached by going up one level. In a PyInstaller-frozen build
    BASE_PATH is sys._MEIPASS (the extracted bundle root); window_control
    .spec stages apps/web/out's contents as a top-level "web" datas entry
    there, mirroring how "assets" and (previously) "client" were staged --
    NOT as "../apps/web/out", which wouldn't exist inside the bundle.
    """
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(BASE_PATH, "web")
    return os.path.join(os.path.dirname(BASE_PATH), "apps", "web", "out")


WEB_BUILD_DIR = get_web_build_dir()


def engine_exe_path() -> str:
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(ASSETS_DIR, "engine", "engine.exe")
    return os.path.join(
        os.path.dirname(BASE_PATH), "engine", "build", "Release", "engine.exe"
    )
