import os
import sys

PORT = 8080
DEV_MODE = sys.platform != "win32"
VERSION = "2.3.22"
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
VPS_SIGNALING_URL = os.environ.get("VPS_SIGNALING_URL")  # e.g. "ws://VPS_IP:8443"; None disables the public bridge path
ENGINE_SIGNALING_SECRET = os.environ.get("ENGINE_SIGNALING_SECRET", "")
ENGINE_LOCAL_ICE_SERVERS = tuple(filter(None, os.environ.get(
    "ENGINE_LOCAL_ICE_SERVERS", ""
).split(",")))
ENGINE_PUBLIC_ICE_SERVERS = tuple(filter(None, os.environ.get(
    "ENGINE_PUBLIC_ICE_SERVERS", ""
).split(",")))
# TURN (+ a public STUN fallback) for the *public* WebRTC path specifically
# (initWebRTCPublic() in the client) -- the local/Tailscale path above keeps
# using the embedded STUN_PORT server unchanged. A NAT'd PC has no publicly
# reachable ICE candidate on its own; without a TURN relay, ICE on the public
# path fails after signaling succeeds and the client silently falls back to
# local WHEP (unreachable off-Tailscale) -- see ice_config.py. TURN is
# optional: absent TURN_HOST, get_ice_servers() returns STUN-only.
TURN_HOST = os.environ.get("TURN_HOST")
TURN_PORT = os.environ.get("TURN_PORT", "3478")
TURN_USERNAME = os.environ.get("TURN_USERNAME")
TURN_CREDENTIAL = os.environ.get("TURN_CREDENTIAL")
# NOTE: if the web client is ever served over HTTPS, this must be "wss://" —
# browsers block plaintext ws:// as mixed content under HTTPS, which fails
# silently (ws.onerror fires, client falls back to a local URL a public
# client can't reach). No TLS termination exists yet, so this is still
# ws://. Also: session ids on the VPS signaling relay are sequential/
# enumerable and there is no auth on that path yet — not safe to expose
# publicly without the planned follow-up auth work.

# Supabase project — unset SUPABASE_URL = auth disabled (LAN-only /
# trusted-network deployments). Set before exposing the app past a
# trusted LAN — every route is otherwise open to anyone with the URL.
SUPABASE_URL = os.environ.get("SUPABASE_URL")
# Public, safe to ship to browser/mobile/tray clients — used only to talk
# to Supabase's Auth REST API directly for login/register.
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
# Server-only. Supabase's legacy JWT secret — verifies the HS256
# signature on every access token locally, no network round trip.
SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")
# Server-only. Full-access Postgres REST credential used solely by
# supabase_client.py for the device_links table, after FastAPI has
# already authenticated the caller and is enforcing ownership itself.
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")

# Public-internet UI tunnel (VPS relay). Unset = tunnel disabled, matching
# the VPS_SIGNALING_URL auto-start-only-if-configured pattern above. Full
# URL including path, e.g. "wss://tunnel.example.com/__tunnel/register".
PUBLIC_UI_URL = os.environ.get("PUBLIC_UI_URL")
# Authenticates the tunnel *link* (PC <-> VPS), separate from AUTH_TOKEN
# which authenticates the *browser user* — a leaked one doesn't compromise
# the other. Required whenever PUBLIC_UI_URL is set.
TUNNEL_SECRET = os.environ.get("TUNNEL_SECRET")

ADB_PATH = "adb"       # overridden at runtime by _find_adb()
SCRCPY_PATH = os.path.join("assets", "scrcpy", "scrcpy.exe")


def get_base_path():
    if hasattr(sys, '_MEIPASS'):
        return sys._MEIPASS
    return os.path.dirname(os.path.abspath(__file__))


BASE_PATH = get_base_path()
CLIENT_DIR = os.path.join(BASE_PATH, "client")
ASSETS_DIR = os.path.join(BASE_PATH, "assets")


def engine_exe_path() -> str:
    if hasattr(sys, "_MEIPASS"):
        return os.path.join(ASSETS_DIR, "engine", "engine.exe")
    return os.path.join(
        os.path.dirname(BASE_PATH), "engine", "build", "Release", "engine.exe"
    )
