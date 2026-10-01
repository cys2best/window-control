import asyncio
import io
import logging
import os
import re
import struct
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.requests import HTTPConnection
from starlette.routing import Match

from config import WEB_BUILD_DIR, STUN_PORT, TIER_ORDER
from server import adb_manager
from server.instance_manager import InstanceManager
from server.network_gate import is_allowed_peer, is_loopback_peer
from server.pairing import PairingStore, bearer_token
from server.tailscale import get_best_ip

log = logging.getLogger(__name__)

# Routes an unpaired device may load: the pairing API and the static app
# shell that renders the pairing screen. Each is build output with no user
# data; the protected data lives behind the JSON API routes, which stay
# gated.
_PAIRING_EXEMPT_PATHS = {
    "/", "/pair", "/pair/status", "/stream",
    "/index.txt", "/pair.txt", "/stream.txt", "/instances.txt", "/account.txt",
    "/manifest.json", "/icon-192.png", "/icon-512.png", "/favicon.ico", "/404.html",
}

# apps/web's static export emits one `<route>.txt` file per route. Only
# flat, alphanumeric names exist; the route below refuses anything else so a
# crafted name can never escape WEB_BUILD_DIR through os.path.join (on
# Windows a backslash is a separator too, and `{page}`'s default converter
# allows it).
_RSC_PAYLOAD_NAME = re.compile(r"[A-Za-z0-9_-]+")


def _prefers_html(request: HTTPConnection) -> bool:
    """True when the caller is a browser doing a top-level navigation.

    Browsers send `Accept: text/html,...` for document navigations;
    packages/core's API client and apps/mobile use plain `fetch()` with no
    Accept header at all (default `*/*`), so this cleanly separates "load
    the page" from "give me the JSON list" on the one path that must do
    both. Used by BOTH the access gate and GET /instances, deliberately the
    same single predicate on the same request — if the two ever disagreed,
    an unpaired request could be waved past the gate and then answered with
    real instance data.
    """
    return "text/html" in request.headers.get("accept", "")


def _is_pairing_exempt(scope) -> bool:
    path = scope["path"]
    if path in _PAIRING_EXEMPT_PATHS or path.startswith("/_next/"):
        return True
    # Browser navigation shells have no user data, while API-shaped requests
    # on the shared paths remain protected.
    return (
        scope["method"] == "GET"
        and path in {"/instances", "/account"}
        and _prefers_html(HTTPConnection(scope))
    )


def _request_token(request: HTTPConnection) -> str | None:
    return bearer_token(request.headers.get("authorization")) or request.query_params.get("token")


_LOCAL_HOST_NAMES = frozenset({"127.0.0.1", "localhost", "[::1]"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _host_name(host_header: str | None) -> str | None:
    """Lower-cased hostname of a Host header value, without the port."""
    if not host_header:
        return None
    value = host_header.strip().lower()
    if value.startswith("["):
        end = value.find("]")
        return value[: end + 1] if end != -1 else None
    return value.split(":", 1)[0]


def is_trusted_loopback(peer: str | None, headers, method: str = "GET") -> bool:
    """Whether a request comes from the owner's own machine, not a rebinding page.

    A loopback peer alone is not enough: a page that DNS-rebinds its own name
    to 127.0.0.1 also arrives from loopback, but carries its own Host. And a
    cross-site browser POST to 127.0.0.1 carries `Sec-Fetch-Site: cross-site`.
    """
    if not is_loopback_peer(peer):
        return False
    if _host_name(headers.get("host")) not in _LOCAL_HOST_NAMES:
        return False
    if method.upper() not in _SAFE_METHODS and headers.get("sec-fetch-site", "").lower() == "cross-site":
        return False
    return True


class AccessGate:
    """Network allowlist, then device pairing.

    Pure ASGI rather than `@app.middleware("http")` so WebSocket scopes are
    covered too: an HTTP-only middleware would let a future WebSocket route
    bypass both checks.
    """

    def __init__(self, app, pairing: PairingStore):
        self.app = app
        self.pairing = pairing

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        client = scope.get("client")
        host = client[0] if client else None
        if not is_allowed_peer(host):
            await self._reject(scope, receive, send, 403, "Forbidden")
            return
        connection = HTTPConnection(scope)
        method = scope.get("method", "GET")
        if not is_trusted_loopback(host, connection.headers, method) and self._needs_token(scope):
            if not self.pairing.is_valid_token(_request_token(connection)):
                await self._reject(scope, receive, send, 401, "Not paired")
                return
        await self.app(scope, receive, send)

    def _needs_token(self, scope) -> bool:
        if scope["type"] == "websocket":
            return True
        if _is_pairing_exempt(scope):
            return False
        # Only gate paths that resolve to a registered route, so an unknown
        # path still falls through to the router's normal 404.
        return any(
            route.matches(scope)[0] != Match.NONE
            for route in scope["app"].router.routes
        )

    async def _reject(self, scope, receive, send, status: int, detail: str):
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
        response = JSONResponse({"detail": detail}, status_code=status, headers=headers)
        await response(scope, receive, send)


def _log(msg: str):
    for _p in [r"C:\ProgramData\EmuCtrl", r"C:\Windows\Temp"]:
        try:
            os.makedirs(_p, exist_ok=True)
            with open(os.path.join(_p, "service_crash.log"), "a") as f:
                f.write(msg + "\n")
            return
        except Exception:
            continue


class SelectRequest(BaseModel):
    id: str  # "adb:SERIAL"


class QualityTierRequest(BaseModel):
    tier: str


def _make_exception_handler(default_handler):
    def handler(loop, context):
        exc = context.get("exception")
        if isinstance(exc, ConnectionResetError):
            return
        if isinstance(exc, OSError) and getattr(exc, "winerror", None) == 10054:
            return
        if default_handler:
            default_handler(loop, context)
        else:
            loop.default_exception_handler(context)
    return handler


def _decode_raw_screencap(raw: bytes):
    """Parse Android's raw `screencap` (no -p) framebuffer dump.

    Format (frameworks/base cmds/screencap): 4-byte LE width, 4-byte LE
    height, 4-byte LE PixelFormat, optionally a 4-byte LE dataSpace field
    added in later Android versions -- so the header is either 12 or 16
    bytes -- followed by width*height*4 raw pixel bytes. Only PixelFormat 1
    (RGBA_8888) and 4 (RGBX_8888) are handled; both are 4 bytes/pixel and
    the 4th byte is discarded on JPEG conversion either way, so they're
    treated identically. Returns None (caller falls back to `-p`/PNG) for
    any header/format this doesn't recognize -- there's no device in CI to
    verify every Android build's exact layout against, so an unrecognized
    header must degrade, not crash or produce a corrupt image.
    """
    from PIL import Image

    for header_len in (16, 12):
        if len(raw) <= header_len:
            continue
        w, h, fmt = struct.unpack_from("<III", raw, 0)
        if fmt not in (1, 4) or w <= 0 or h <= 0:
            continue
        if len(raw) - header_len != w * h * 4:
            continue
        return Image.frombuffer("RGBA", (w, h), raw[header_len:], "raw", "RGBA", 0, 1)
    return None


async def _capture_preview(serial: str) -> Response:
    """Grab a device screenshot and return a small JPEG thumbnail.

    Prefers raw (no `-p`) screencap: `-p` makes the LDPlayer host do a
    device-side lossless PNG encode for a thumbnail that gets re-encoded to
    JPEG a moment later anyway. Raw capture ships the uncompressed
    framebuffer instead, decoded here with PIL.frombuffer (no
    decompression needed) and JPEG-encoded with Pillow's own
    libjpeg-turbo-backed encoder. Falls back to the old `-p` PNG path
    whenever the raw header doesn't parse (see _decode_raw_screencap) --
    this preview is best-effort, not load-bearing, so a decode miss should
    degrade, not fail the request.

    Both the adb subprocess (up to ~5s) and the PIL encode run off the
    event loop so a preview fetch never freezes concurrent requests --
    including concurrent selection and preview requests.
    """
    import asyncio as _asyncio
    import urllib.parse
    from PIL import Image

    serial = urllib.parse.unquote(serial)
    if serial.startswith("adb:"):
        serial = serial[4:]
    if not serial:
        raise HTTPException(status_code=400, detail="Serial required")

    adb = adb_manager._find_adb()
    if not adb:
        raise HTTPException(status_code=503, detail="adb not found")
    nw = adb_manager._no_window_flags()

    def _encode(img) -> bytes:
        img.thumbnail((640, 384))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85)
        return buf.getvalue()

    def _grab_raw():
        raw = subprocess.check_output(
            [adb, "-s", serial, "exec-out", "screencap"],
            timeout=5, **nw,
        )
        img = _decode_raw_screencap(raw)
        return _encode(img) if img is not None else None

    def _grab_png() -> bytes:
        png = subprocess.check_output(
            [adb, "-s", serial, "exec-out", "screencap -p"],
            timeout=5, **nw,
        )
        return _encode(Image.open(io.BytesIO(png)))

    try:
        data = await _asyncio.to_thread(_grab_raw)
        if data is None:
            data = await _asyncio.to_thread(_grab_png)
    except Exception:
        raise HTTPException(status_code=503, detail="Preview capture failed")
    return Response(content=data, media_type="image/jpeg")




def _format_host(host: str) -> str:
    if host.startswith("[") and host.endswith("]"):
        return host
    return host if ":" not in host else f"[{host}]"


def _selection_ice_servers(host: str) -> list[dict]:
    return [{"urls": f"stun:{_format_host(host)}:{STUN_PORT}"}]


class PairRequest(BaseModel):
    code: str
    device_name: str = ""


def create_app(instance_manager: InstanceManager,
               pairing: PairingStore | None = None) -> FastAPI:
    import asyncio
    if pairing is None:
        pairing = PairingStore()
    app = FastAPI()
    app.add_middleware(AccessGate, pairing=pairing)

    @app.on_event("startup")
    async def _startup():
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(_make_exception_handler(loop.get_exception_handler()))

        # Discover LDPlayer instances on startup
        import threading
        threading.Thread(target=instance_manager.refresh, daemon=True).start()

    # ── Static / index ───────────────────────────────────────────────────────
    # apps/web (Next.js, output: "export") replaces the old hand-rolled
    # src/client single-page app. Its build emits one static HTML file per
    # route (index/login/setup/instances/stream) plus content-hashed
    # `_next/static/**` chunk filenames -- e.g. `main-app-<hash>.js` -- so
    # the old ?v=<VERSION> query-string cache-busting rewrite (which existed
    # solely because the previous client's app.js/style.css URLs never
    # changed on their own) is redundant here and has been removed: a
    # content change always changes the hash, which already forces a fresh
    # fetch. Confirmed by inspecting a real `npm run build -w apps/web`
    # output's index.html rather than assumed.
    #
    # `/instances` is served by BOTH the page shell and the JSON API, on
    # the one path, split by content negotiation (see get_instances) --
    # the JSON contract packages/core and apps/mobile call stays exactly
    # as it was, while a browser navigating there gets the app shell
    # instead of a page of raw JSON.

    def _serve_web_page(filename: str) -> HTMLResponse:
        html_path = os.path.join(WEB_BUILD_DIR, filename)
        if os.path.exists(html_path):
            html = Path(html_path).read_text()
            return HTMLResponse(
                html,
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )
        return HTMLResponse("<h1>Client not found</h1>", status_code=500)

    def _serve_web_file(filename: str, media_type: str, headers=None) -> Response:
        file_path = os.path.join(WEB_BUILD_DIR, filename)
        if not os.path.isfile(file_path):
            raise HTTPException(status_code=404, detail="Not found")
        return Response(
            content=Path(file_path).read_bytes(),
            media_type=media_type,
            headers=headers,
        )

    @app.get("/")
    async def index():
        return _serve_web_page("index.html")

    @app.get("/pair")
    async def pair_page():
        return _serve_web_page("pair.html")

    @app.post("/pair")
    async def pair_device(req: PairRequest):
        token = await asyncio.to_thread(pairing.pair, req.code, req.device_name)
        if token is None:
            raise HTTPException(status_code=403, detail="Invalid or expired pairing code")
        return {"token": token}

    @app.get("/pair/status")
    async def pair_status(request: Request):
        host = request.client.host if request.client else None
        return {
            "paired": is_trusted_loopback(host, request.headers, request.method)
            or pairing.is_valid_token(_request_token(request)),
        }

    @app.get("/stream")
    async def stream_page():
        return _serve_web_page("stream.html")

    @app.get("/account")
    async def account_page():
        return _serve_web_page("account.html")

    @app.get("/404.html")
    async def not_found_page():
        return _serve_web_page("404.html")

    @app.get("/manifest.json")
    async def web_manifest():
        # PWA installability + the standalone/status-bar behavior the old
        # src/client/manifest.json provided; apps/web ships it from
        # apps/web/public/, which Next copies verbatim into out/.
        return _serve_web_file("manifest.json", "application/manifest+json")

    @app.get("/icon-192.png")
    async def web_icon():
        return _serve_web_file("icon-192.png", "image/png")

    @app.get("/icon-512.png")
    async def web_icon_512():
        return _serve_web_file("icon-512.png", "image/png")

    @app.get("/favicon.ico")
    async def web_favicon():
        return _serve_web_file("favicon.ico", "image/x-icon")

    @app.get("/{page}.txt")
    async def web_rsc_payload(page: str):
        """Serve the static export's prerendered RSC payloads.

        Next 15's client-side router does not fetch the HTML on a soft
        navigation -- for `router.replace("/instances")` (and every Link
        click) it fetches `/instances.txt`, the build-time RSC payload, and
        falls back to a full `window.location` page load whenever that
        response is missing or not `ok`. Without this route every in-app
        navigation degraded into a hard reload, which for the app's own
        default post-login destination meant landing on the JSON API.

        Content type must be `text/x-component` or `text/plain`: the
        router accepts only those two (verified in the shipped router
        chunk) and hard-navigates otherwise.
        """
        if not _RSC_PAYLOAD_NAME.fullmatch(page):
            raise HTTPException(status_code=404, detail="Not found")
        # Fixed filenames whose contents change every build (unlike the
        # content-hashed /_next chunks), exactly like the HTML shells --
        # so they get the same no-cache treatment.
        return _serve_web_file(
            f"{page}.txt", "text/x-component; charset=utf-8",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    # ── Instance management ──────────────────────────────────────────────────

    @app.get("/instances")
    async def get_instances(request: Request):
        """JSON instance list, or apps/web's instance-list page shell.

        One path, two consumers: packages/core's API client and
        apps/mobile call this for the JSON list (plain fetch, no Accept
        header), while apps/web's own `/instances` route is where the app
        lands after login -- a browser reload or hard navigation there
        must render the app, not a page of raw JSON. The JSON contract is
        untouched; only an explicitly HTML-preferring request branches.
        """
        if _prefers_html(request):
            return _serve_web_page("instances.html")
        return instance_manager.list_instances()

    @app.post("/instances/{instance_id}/select")
    async def select_instance(instance_id: str, request: Request):
        inst = instance_manager.get(instance_id)
        if inst is None:
            raise HTTPException(status_code=404, detail="Instance not found")
        host = get_best_ip() or (request.client.host if request.client else "127.0.0.1")
        selection = await asyncio.to_thread(instance_manager.select, instance_id, host)
        if selection is None:
            raise HTTPException(status_code=503, detail="Engine runtime not ready")

        return {
            "ok": True,
            "id": inst.id,
            "serial": inst.serial,
            "name": inst.name,
            "w": selection.width,
            "h": selection.height,
            "whep_url": selection.whep_url,
            "whep_token": selection.whep_token,
            "ice_servers": _selection_ice_servers(host),
            "generation": selection.generation,
        }

    @app.post("/instances/{instance_id}/keyframe")
    async def request_keyframe(instance_id: str, request: Request):
        """Ask an instance's encoder to emit an IDR now (switch prefetch).

        The list page fires this on touchstart/hover of a tile — before the user
        even releases the tap — so by the time the switch's WHEP negotiates, a
        fresh keyframe is already in flight and the new stream paints instantly.
        Copy-mux has no ffmpeg GOP, so this source-side IDR is what makes a switch
        fast. Best-effort and fire-and-forget: unknown instance or an unconnected
        control socket is a silent no-op (the 2s heartbeat and select()'s own
        request_idr still cover it).
        """
        await asyncio.to_thread(instance_manager.request_keyframe, instance_id)
        return {"ok": True}

    @app.post("/instances/{instance_id}/quality")
    async def set_instance_quality(
        instance_id: str, req: QualityTierRequest, request: Request
    ):
        """Set stream quality tier for an instance."""
        if req.tier not in TIER_ORDER:
            raise HTTPException(status_code=400, detail="Invalid tier")
        if instance_manager.get(instance_id) is None:
            raise HTTPException(status_code=404, detail="Instance not found")
        # set_tier does ~1.8s of blocking scrcpy restart — offload off the loop.
        ok = await asyncio.to_thread(instance_manager.set_tier, instance_id, req.tier)
        if not ok:
            raise HTTPException(status_code=404, detail="Instance not found")
        return {"ok": True, "tier": req.tier}

    @app.get("/instances/{instance_id}/preview")
    @app.get("/preview/{instance_id}")
    async def instance_preview(instance_id: str, request: Request):
        return await _capture_preview(instance_id)

    @app.get("/instances/preview")
    @app.get("/preview")
    async def query_preview(request: Request):
        serial = request.query_params.get("serial") or request.query_params.get("id") or ""
        if not serial:
            active = instance_manager.active
            if active:
                serial = active.serial
            else:
                instances = instance_manager.list_instances()
                if instances:
                    serial = instances[0].get("serial", "")
        if not serial:
            raise HTTPException(status_code=404, detail="No instance found")
        return await _capture_preview(serial)

    # ── Legacy /windows + /select (kept for backward compat) ────────────────

    @app.get("/windows")
    async def get_windows(request: Request):
        return instance_manager.list_instances()

    @app.post("/select")
    async def select_window(req: SelectRequest, request: Request):
        if not req.id.startswith("adb:"):
            raise HTTPException(status_code=400, detail="Invalid id — must be adb:SERIAL")
        serial = req.id[4:]
        host = get_best_ip() or (request.client.host if request.client else "127.0.0.1")
        # Selection and refresh do blocking network/subprocess work — offload.
        selection = await asyncio.to_thread(instance_manager.select, serial, host)
        if selection is None:
            # Instance may not be discovered yet — try refresh
            await asyncio.to_thread(instance_manager.refresh)
            selection = await asyncio.to_thread(instance_manager.select, serial, host)
        if selection is None:
            raise HTTPException(status_code=404, detail="Instance not found")
        inst = instance_manager.active
        if inst is None:
            raise HTTPException(status_code=404, detail="Instance disappeared")

        return {"ok": True, "id": req.id, "name": inst.name,
                "w": selection.width, "h": selection.height,
                "whep_url": selection.whep_url,
                "stun_url": f"stun:{host}:{STUN_PORT}",
                "ice_servers": _selection_ice_servers(host)}

    # ── Preview (legacy URL) ─────────────────────────────────────────────────

    @app.get("/window/{window_id}/preview")
    async def preview(window_id: str, request: Request):
        return await _capture_preview(window_id)

    # apps/web's content-hashed asset chunks (main-app-<hash>.js, etc.) --
    # every <script src> in its exported HTML is rooted at "/_next/...",
    # so this must be mounted at "/_next" to match, not "/static" (the old
    # client's assets lived under a "/static" prefix by choice, not by any
    # framework requirement). Mounted last, after every @app.get/@app.post
    # route above and the four page routes just registered, so it can only
    # ever catch paths none of those already claimed.
    _next_dir = os.path.join(WEB_BUILD_DIR, "_next")
    if os.path.isdir(_next_dir):
        app.mount("/_next", StaticFiles(directory=_next_dir), name="web_static")

    return app
