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
from starlette.routing import Match

from config import WEB_BUILD_DIR, STUN_PORT, TIER_ORDER
from server import adb_manager
from server import auth
from server import install_identity
from server.ice_config import get_ice_servers
from server.instance_manager import InstanceManager
from server.http_tunnel import run_tunnel_with_reconnect
from server.supabase_client import SupabaseClient, SupabaseUnavailable
from server.tailscale import get_best_ip

log = logging.getLogger(__name__)

_tunnel_task: "asyncio.Task | None" = None

# Routes reachable without a JWT even when Supabase auth is enabled —
# just enough to load the login/register UI and its Supabase config. These
# are apps/web's page-shell HTML routes, the matching build-time-prerendered
# `.txt` RSC payloads its client-side router fetches for the same shells,
# and the PWA manifest/icon: each is static build output with no embedded
# user data (same reasoning that exempted the old single-page client's "/"
# wholesale) -- the actual protected data lives behind the JSON API routes
# below, which stay gated.
_AUTH_EXEMPT_PATHS = {
    "/", "/login", "/stream", "/auth/config",
    "/index.txt", "/login.txt", "/stream.txt", "/instances.txt",
    "/manifest.json", "/icon-192.png", "/404.html",
}

# apps/web's static export emits one `<route>.txt` file per route. Only
# flat, alphanumeric names exist (index/login/setup/stream/instances); the
# route below refuses anything else so a crafted name can never escape
# WEB_BUILD_DIR through os.path.join (on Windows a backslash is a
# separator too, and `{page}`'s default converter allows it).
_RSC_PAYLOAD_NAME = re.compile(r"[A-Za-z0-9_-]+")


def _prefers_html(request: Request) -> bool:
    """True when the caller is a browser doing a top-level navigation.

    Browsers send `Accept: text/html,...` for document navigations;
    packages/core's API client and apps/mobile use plain `fetch()` with no
    Accept header at all (default `*/*`), so this cleanly separates "load
    the page" from "give me the JSON list" on the one path that must do
    both. Used by BOTH the auth gate and GET /instances, deliberately the
    same single predicate on the same request — if the two ever disagreed,
    an unauthenticated request could be waved past the gate and then
    answered with real instance data.
    """
    return "text/html" in request.headers.get("accept", "")


def _is_public_web_asset(request: Request) -> bool:
    """Static apps/web build output that must load before/without a login."""
    path = request.url.path
    if path in _AUTH_EXEMPT_PATHS or path.startswith("/_next/"):
        return True
    # A browser navigating to /instances gets the page shell (see
    # get_instances) -- the identical static file every other exempt shell
    # serves. The JSON list on that same path stays gated.
    return request.method == "GET" and path == "/instances" and _prefers_html(request)


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


def current_user(request: Request) -> auth.UserClaims | None:
    token = auth.bearer_token(request.headers.get("authorization"))
    if not token:
        token = request.query_params.get("token")
    return auth.verify_supabase_jwt(token)


def _format_host(host: str) -> str:
    if host.startswith("[") and host.endswith("]"):
        return host
    return host if ":" not in host else f"[{host}]"


def _selection_ice_servers(host: str) -> list[dict]:
    """Place embedded request-host STUN first and de-duplicate URLs in order."""
    servers = [{"urls": f"stun:{_format_host(host)}:{STUN_PORT}"}]
    seen = {servers[0]["urls"]}
    for server in get_ice_servers():
        urls = server.get("urls")
        values = urls if isinstance(urls, list) else [urls]
        unique = []
        for url in values:
            if url and url not in seen:
                seen.add(url)
                unique.append(url)
        if not unique:
            continue
        item = dict(server)
        item["urls"] = unique if isinstance(urls, list) else unique[0]
        servers.append(item)
    return servers


def create_app(instance_manager: InstanceManager) -> FastAPI:
    import asyncio
    from config import (
        PUBLIC_UI_URL, TUNNEL_SECRET, SUPABASE_URL, SUPABASE_ANON_KEY,
        SUPABASE_SERVICE_ROLE_KEY,
    )
    if PUBLIC_UI_URL and not auth.auth_enabled():
        raise RuntimeError("PUBLIC_UI_URL requires SUPABASE_URL to be set")
    if PUBLIC_UI_URL and not TUNNEL_SECRET:
        raise RuntimeError("PUBLIC_UI_URL requires TUNNEL_SECRET to be set")
    if os.environ.get("AUTH_TOKEN") and not auth.auth_enabled():
        log.warning(
            "AUTH_TOKEN is set but SUPABASE_URL is not — AUTH_TOKEN no longer "
            "does anything (it was replaced by Supabase auth); this deployment "
            "is now UNAUTHENTICATED. Set SUPABASE_URL to re-enable auth, or "
            "unset AUTH_TOKEN to acknowledge LAN-only mode."
        )
    app = FastAPI()

    supabase = SupabaseClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY) if auth.auth_enabled() else None
    _install_public_key = None
    if auth.auth_enabled():
        _, _install_public_key = install_identity.get_or_create_install_keypair()
    _cached_owner_user_id = install_identity.get_cached_owner_user_id()

    @app.middleware("http")
    async def _auth_gate(request: Request, call_next):
        request.state.user = None
        # This middleware runs before Starlette's router does path matching,
        # so a naive "not exempt -> require auth" check would 401 requests
        # to *nonexistent* routes too (e.g. the removed POST /login) instead
        # of letting them fall through to the router's normal 404. Only gate
        # paths that actually resolve to a registered route.
        if auth.auth_enabled() and not _is_public_web_asset(request) \
                and any(route.matches(request.scope)[0] != Match.NONE
                         for route in app.router.routes):
            user = current_user(request)
            if user is None:
                return JSONResponse(
                    {"detail": "Not authenticated"}, status_code=401,
                    headers={"WWW-Authenticate": "Bearer"},
                )
            request.state.user = user

            nonlocal _cached_owner_user_id
            if _cached_owner_user_id is None:
                # First-use claim: this install has no owner yet, adopt
                # whoever successfully authenticates first.
                # Best-effort: a Supabase hiccup here must not fail this
                # unrelated request. The cache only advances on success, so
                # the next request naturally retries.
                try:
                    await asyncio.to_thread(supabase.upsert_install, user.user_id, _install_public_key)
                except SupabaseUnavailable:
                    pass
                else:
                    install_identity.set_cached_owner_user_id(user.user_id)
                    _cached_owner_user_id = user.user_id
            elif user.user_id != _cached_owner_user_id:
                # A different account than this install's already-claimed
                # owner. Deliberately does NOT re-upsert or adopt -- an
                # authenticated HTTP request alone must never be able to
                # transfer ownership of an already-claimed install away
                # from its real owner. Switching owners requires local
                # filesystem access to this machine (delete
                # install_owner.txt, see install_identity.py), matching the
                # "attacker needs filesystem access to this specific PC"
                # bar the rest of this design already assumes.
                return JSONResponse(
                    {"detail": "This install belongs to a different account"},
                    status_code=403,
                )
        return await call_next(request)

    @app.on_event("startup")
    async def _startup():
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(_make_exception_handler(loop.get_exception_handler()))

        # Discover LDPlayer instances on startup
        import threading
        threading.Thread(target=instance_manager.refresh, daemon=True).start()

        global _tunnel_task
        if PUBLIC_UI_URL:
            log.info("tunnel: starting task for %s", PUBLIC_UI_URL)
            _tunnel_task = asyncio.create_task(
                run_tunnel_with_reconnect(PUBLIC_UI_URL, TUNNEL_SECRET))

    @app.on_event("shutdown")
    async def _shutdown():
        global _tunnel_task
        if _tunnel_task is not None and not _tunnel_task.done():
            log.info("tunnel: cancelling task on shutdown")
            _tunnel_task.cancel()
            try:
                await _tunnel_task
            except asyncio.CancelledError:
                pass

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

    @app.get("/login")
    async def login_page():
        return _serve_web_page("login.html")

    @app.get("/stream")
    async def stream_page():
        return _serve_web_page("stream.html")

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

    @app.get("/auth/config")
    async def auth_config():
        return {
            "auth_enabled": auth.auth_enabled(),
            "supabase_url": SUPABASE_URL or "",
            "supabase_anon_key": SUPABASE_ANON_KEY,
        }

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
            "signaling_url": selection.signaling_url,
            "public_session": selection.public_session,
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
                "signaling_url": selection.signaling_url,
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
