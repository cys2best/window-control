import asyncio
import io
import logging
import os
import subprocess
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Literal

from config import CLIENT_DIR, COOKIE_SECURE, QUALITY_MAP, WHEP_PORT, STUN_PORT, TIER_ORDER, VPS_SIGNALING_URL
from server.stream import CaptureState, FrameQueue, mjpeg_generator
from server import adb_manager
from server import auth
from server.ice_config import get_ice_servers
from server.instance_manager import InstanceManager
from server.http_tunnel import run_tunnel_with_reconnect
from server.signaling_bridge import run_bridge_with_reconnect
from server.tailscale import get_best_ip

log = logging.getLogger(__name__)

_bridge_task: "asyncio.Task | None" = None
_tunnel_task: "asyncio.Task | None" = None

# Routes reachable without a session cookie even when AUTH_TOKEN is set —
# just enough to load the login gate and let it authenticate.
_AUTH_EXEMPT_PATHS = {"/", "/login"}


def _is_localhost(host: str | None) -> bool:
    return host in ("127.0.0.1", "::1")


def _log(msg: str):
    for _p in [r"C:\ProgramData\WindowControl", r"C:\Windows\Temp"]:
        try:
            os.makedirs(_p, exist_ok=True)
            with open(os.path.join(_p, "service_crash.log"), "a") as f:
                f.write(msg + "\n")
            return
        except Exception:
            continue


class SelectRequest(BaseModel):
    id: str  # "adb:SERIAL"


class QualityRequest(BaseModel):
    quality: Literal["low", "medium", "high"]


class QualityTierRequest(BaseModel):
    tier: str


class LoginRequest(BaseModel):
    token: str


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


_JS_KEY_TO_KEYCODE = {
    "Return":    66,
    "BackSpace": 67,
    "Tab":       61,
    "Escape":    111,
    "Delete":    112,
    "ArrowLeft": 21,
    "ArrowUp":   19,
    "ArrowRight": 22,
    "ArrowDown": 20,
    " ":         62,
    "Space":     62,
    "Back":      4,
    "Home":      3,
    "Menu":      82,
}


def _dispatch_key_control(ctrl, key: str):
    kc = _JS_KEY_TO_KEYCODE.get(key)
    if kc:
        ctrl.send_keycode(kc)


async def _capture_preview(serial: str) -> Response:
    """Grab a device screenshot and return a small JPEG thumbnail.

    `screencap -p` is a blocking subprocess up to ~5s; run it (and the PIL
    encode) off the event loop so a preview fetch never freezes concurrent
    requests — including the /input WebSocket, which would otherwise stall taps
    while a thumbnail loads.
    """
    import asyncio as _asyncio
    from PIL import Image

    adb = adb_manager._find_adb()
    if not adb:
        raise HTTPException(status_code=503, detail="adb not found")
    nw = adb_manager._no_window_flags()

    def _grab() -> bytes:
        png = subprocess.check_output(
            [adb, "-s", serial, "exec-out", "screencap -p"],
            timeout=5, **nw,
        )
        img = Image.open(io.BytesIO(png))
        img.thumbnail((640, 384))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=85)
        return buf.getvalue()

    try:
        data = await _asyncio.to_thread(_grab)
    except Exception:
        raise HTTPException(status_code=503, detail="Preview capture failed")
    return Response(content=data, media_type="image/jpeg")


def _restart_bridge_task(instance_name: str) -> None:
    """(Re)start the public signaling bridge for the newly-selected instance.

    Cancels any bridge task already running for a previously-selected
    instance, then starts a fresh one for `instance_name` if a public
    signaling VPS is configured. No-ops (leaving `_bridge_task` as None)
    when VPS_SIGNALING_URL is unset.
    """
    global _bridge_task
    if _bridge_task is not None and not _bridge_task.done():
        log.info("bridge: cancelling existing task for switch to %s", instance_name)
        _bridge_task.cancel()
    if VPS_SIGNALING_URL:
        log.info("bridge: starting task for %s", instance_name)
        _bridge_task = asyncio.create_task(
            run_bridge_with_reconnect(instance_name, VPS_SIGNALING_URL, WHEP_PORT)
        )
    else:
        _bridge_task = None


def create_app(state: CaptureState, frame_queue: FrameQueue,
               instance_manager: InstanceManager) -> FastAPI:
    import asyncio
    from config import PUBLIC_UI_URL, TUNNEL_SECRET
    if PUBLIC_UI_URL and not auth.auth_enabled():
        raise RuntimeError("PUBLIC_UI_URL requires AUTH_TOKEN to be set")
    if PUBLIC_UI_URL and not TUNNEL_SECRET:
        raise RuntimeError("PUBLIC_UI_URL requires TUNNEL_SECRET to be set")
    app = FastAPI()

    @app.middleware("http")
    async def _auth_gate(request: Request, call_next):
        if request.url.path.startswith("/internal/"):
            if not _is_localhost(request.client.host if request.client else None):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            return await call_next(request)
        if auth.auth_enabled() and request.url.path not in _AUTH_EXEMPT_PATHS \
                and not request.url.path.startswith("/static/"):
            if not auth.verify_session_cookie(request.cookies.get(auth.COOKIE_NAME)):
                return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return await call_next(request)

    @app.post("/login")
    async def login(req: LoginRequest, response: Response):
        if not auth.check_token(req.token):
            raise HTTPException(status_code=401, detail="Invalid token")
        response.set_cookie(
            auth.COOKIE_NAME, auth.make_session_cookie(),
            max_age=auth.SESSION_MAX_AGE_SECONDS, httponly=True, samesite="lax",
            secure=COOKIE_SECURE,
        )
        return {"ok": True}

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
        # The public signaling bridge task (if any) is otherwise left
        # dangling on app shutdown -- only the switch case (cancel-then-
        # restart in _restart_bridge_task) tore it down before.
        global _bridge_task
        if _bridge_task is not None and not _bridge_task.done():
            log.info("bridge: cancelling task on shutdown")
            _bridge_task.cancel()
            try:
                await _bridge_task
            except asyncio.CancelledError:
                pass

        global _tunnel_task
        if _tunnel_task is not None and not _tunnel_task.done():
            log.info("tunnel: cancelling task on shutdown")
            _tunnel_task.cancel()
            try:
                await _tunnel_task
            except asyncio.CancelledError:
                pass

    # ── Static / index ───────────────────────────────────────────────────────

    @app.get("/")
    async def index():
        html_path = os.path.join(CLIENT_DIR, "index.html")
        if os.path.exists(html_path):
            html = Path(html_path).read_text()
            # Cache-bust the static asset URLs with the app version. The installed
            # iOS PWA caches /static/*.js hard and has no service worker to purge,
            # so after a client change it kept running the old app.js (which hit
            # the removed /active/whep) → white screen. Appending ?v=<version>
            # makes the URL change whenever we ship, forcing a fresh fetch.
            from config import VERSION
            html = html.replace('.js"', f'.js?v={VERSION}"')
            html = html.replace('.css"', f'.css?v={VERSION}"')
            return HTMLResponse(
                html,
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )
        return HTMLResponse("<h1>Client not found</h1>", status_code=500)

    # ── Instance management ──────────────────────────────────────────────────

    @app.get("/instances")
    async def get_instances():
        return instance_manager.list_instances()

    @app.post("/instances/{instance_id}/select")
    async def select_instance(instance_id: str, request: Request):
        """Switch active stream. instance_id is the ADB serial (no prefix)."""
        # select() may start a dead scrcpy session (blocking) — offload so it
        # never stalls the event loop / websocket input path.
        ok = await asyncio.to_thread(instance_manager.select, instance_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Instance not found")
        inst = instance_manager.active
        if inst is None:
            raise HTTPException(status_code=404, detail="Instance disappeared")

        _restart_bridge_task(inst.name)

        host = get_best_ip() or request.client.host
        # WHEP straight to this instance's own always-live path (no 'active' mux).
        whep_url = f"http://{host}:{WHEP_PORT}/{inst.name}/whep"
        return {
            "ok": True,
            "id": inst.id,
            "serial": inst.serial,
            "name": inst.name,
            "w": inst.w,
            "h": inst.h,
            "whep_url": whep_url,
            "stun_url": f"stun:{host}:{STUN_PORT}",
            "signaling_url": VPS_SIGNALING_URL,
            "ice_servers": get_ice_servers(),
        }

    @app.post("/internal/instances/{name}/publish/start")
    async def internal_publish_start(name: str):
        """mediamtx's runOnDemand hook (via publish_hook.py) calls this when
        a WHEP client requests a path with no one publishing yet. Starts
        just the on-demand video half -- the persistent half (control
        socket, input) is already up from discovery, regardless of viewers.
        """
        ok = await asyncio.to_thread(instance_manager.start_video, name)
        return {"ok": ok}

    @app.post("/internal/instances/{name}/publish/stop")
    async def internal_publish_stop(name: str):
        """mediamtx's runOnUnDemand hook calls this runOnDemandCloseAfter
        seconds after the last reader disconnects. Always returns ok:true
        (mediamtx doesn't wait on or retry this the way it does the start
        hook's timeout) -- an unknown/already-gone instance is a no-op in
        InstanceManager.stop_video, not an error.
        """
        await asyncio.to_thread(instance_manager.stop_video, name)
        return {"ok": True}

    @app.post("/instances/{instance_id}/keyframe")
    async def request_keyframe(instance_id: str):
        """Ask an instance's encoder to emit an IDR now (switch prefetch).

        The list page fires this on touchstart/hover of a tile — before the user
        even releases the tap — so by the time the switch's WHEP negotiates, a
        fresh keyframe is already in flight and the new stream paints instantly.
        Copy-mux has no ffmpeg GOP, so this source-side IDR is what makes a switch
        fast. Best-effort and fire-and-forget: unknown instance or an unconnected
        control socket is a silent no-op (the 2s heartbeat and select()'s own
        request_idr still cover it).
        """
        inst = instance_manager.get(instance_id)
        if inst is not None:
            try:
                inst.session.control.request_idr()
            except Exception:
                pass
        return {"ok": True}

    @app.post("/instances/{instance_id}/quality")
    async def set_instance_quality(instance_id: str, req: QualityTierRequest):
        """Set stream quality tier for an instance."""
        if req.tier not in TIER_ORDER:
            raise HTTPException(status_code=400, detail="Invalid tier")
        # set_tier does ~1.8s of blocking scrcpy restart — offload off the loop.
        ok = await asyncio.to_thread(instance_manager.set_tier, instance_id, req.tier)
        if not ok:
            raise HTTPException(status_code=404, detail="Instance not found")
        return {"ok": True, "tier": req.tier}

    @app.get("/instances/{instance_id}/preview")
    async def instance_preview(instance_id: str):
        return await _capture_preview(instance_id)

    # ── Legacy /windows + /select (kept for backward compat) ────────────────

    @app.get("/windows")
    async def get_windows():
        return instance_manager.list_instances()

    @app.post("/select")
    async def select_window(req: SelectRequest, request: Request):
        if not req.id.startswith("adb:"):
            raise HTTPException(status_code=400, detail="Invalid id — must be adb:SERIAL")
        serial = req.id[4:]
        # select()/refresh() do blocking network + subprocess work — offload.
        ok = await asyncio.to_thread(instance_manager.select, serial)
        if not ok:
            # Instance may not be discovered yet — try refresh
            await asyncio.to_thread(instance_manager.refresh)
            ok = await asyncio.to_thread(instance_manager.select, serial)
        if not ok:
            raise HTTPException(status_code=404, detail="Instance not found")
        inst = instance_manager.active
        if inst is None:
            raise HTTPException(status_code=404, detail="Instance disappeared")

        _restart_bridge_task(inst.name)

        host = get_best_ip() or request.client.host
        whep_url = f"http://{host}:{WHEP_PORT}/{inst.name}/whep"
        return {"ok": True, "id": req.id, "name": inst.name, "w": inst.w, "h": inst.h,
                "whep_url": whep_url,
                "stun_url": f"stun:{host}:{STUN_PORT}",
                "signaling_url": VPS_SIGNALING_URL,
                "ice_servers": get_ice_servers()}

    # ── MJPEG fallback stream ────────────────────────────────────────────────

    @app.get("/stream")
    async def stream():
        return StreamingResponse(
            mjpeg_generator(frame_queue, state),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @app.get("/stats")
    async def stats():
        count = state.frames_served
        state.frames_served = 0
        session = state.adb_session
        return {"frames": count, "active": session is not None}

    @app.post("/reconnect")
    async def reconnect():
        session = state.adb_session
        if session is None:
            raise HTTPException(status_code=404, detail="No active session")
        session.stop()
        ok = session.start()
        if not ok:
            raise HTTPException(status_code=503, detail="Could not restart session")
        return {"ok": True}

    # ── Preview (legacy URL) ─────────────────────────────────────────────────

    @app.get("/window/{window_id}/preview")
    async def preview(window_id: str):
        return await _capture_preview(window_id)

    # ── Quality ──────────────────────────────────────────────────────────────

    @app.post("/quality")
    async def set_quality(req: QualityRequest):
        state.set_quality(QUALITY_MAP[req.quality])
        return {"quality": req.quality}

    # ── WebSocket input ──────────────────────────────────────────────────────

    @app.websocket("/input")
    async def ws_input(websocket: WebSocket):
        if auth.auth_enabled() and not auth.verify_session_cookie(
                websocket.cookies.get(auth.COOKIE_NAME)):
            await websocket.close(code=1008)  # policy violation
            return
        await websocket.accept()
        import asyncio as _asyncio
        from server.scrcpy_session import ScrcpyControl

        async def _ping():
            while True:
                await _asyncio.sleep(20)
                try:
                    await websocket.send_text('{"type":"ping"}')
                except Exception:
                    return
        _asyncio.create_task(_ping())

        drag_pos: tuple | None = None
        drag_start_pos: tuple | None = None
        finger_down = False  # track whether touch DOWN was sent (to pair with UP)
        _last_idr_request = 0.0
        try:
            while True:
                data = await websocket.receive_json()
                # Latency probe: echo the client's timestamp straight back so the
                # client can measure input-WS round-trip (client→server→client),
                # isolating input transport latency from video-feedback latency.
                if data.get("type") == "echo":
                    try:
                        await websocket.send_text(
                            '{"type":"echo","t":' + str(data.get("t", 0)) + '}'
                        )
                    except Exception:
                        pass
                    continue
                if data.get("type") == "idr":
                    now = time.monotonic()
                    if now - _last_idr_request >= 0.5:
                        _last_idr_request = now
                        active = instance_manager.active
                        if active is not None:
                            try:
                                active.session.control.request_idr()
                                _log(f"[input] idr requested serial={active.session.serial}")
                            except Exception as exc:
                                _log(f"[input] idr request failed: {exc!r}")
                        else:
                            _log("[input] idr requested but no active instance")
                    continue
                inst = instance_manager.active
                if inst is None:
                    finger_down = False
                    drag_pos = None
                    drag_start_pos = None
                    continue
                try:
                    t = data.get("type")
                    nx, ny = data.get("x", 0.5), data.get("y", 0.5)
                    # Use session dimensions (from scrcpy handshake) — authoritative actual frame size.
                    # Falls back to inst.w/h (from wm size) before session handshake completes.
                    sess = inst.session
                    w, h = sess.w, sess.h
                    ctrl: ScrcpyControl = sess.control

                    if ctrl.connected:
                        # ── Scrcpy control socket path (low-latency) ──────────
                        if t == "click":
                            ctrl.send_touch(ScrcpyControl.ACTION_DOWN, nx, ny, w, h)
                            ctrl.send_touch(ScrcpyControl.ACTION_UP, nx, ny, w, h)
                            finger_down = False
                        elif t == "drag_start":
                            drag_start_pos = (nx, ny)
                            drag_pos = (nx, ny)
                            ctrl.send_touch(ScrcpyControl.ACTION_DOWN, nx, ny, w, h)
                            finger_down = True
                        elif t == "drag_move":
                            if finger_down:
                                ctrl.send_touch(ScrcpyControl.ACTION_MOVE, nx, ny, w, h)
                            drag_pos = (nx, ny)
                        elif t == "drag_end":
                            if finger_down:
                                ctrl.send_touch(ScrcpyControl.ACTION_UP, nx, ny, w, h)
                                finger_down = False
                            drag_pos = None
                            drag_start_pos = None
                        elif t == "scroll":
                            # Two-finger scroll: cancel any active drag first, then swipe
                            if finger_down:
                                ctrl.send_touch(ScrcpyControl.ACTION_UP, nx, ny, w, h)
                                finger_down = False
                            dy = data.get("dy", 0)
                            ny2 = max(0.0, min(1.0, ny + dy * 120 / h)) if h else ny
                            ctrl.send_touch(ScrcpyControl.ACTION_DOWN, nx, ny, w, h)
                            ctrl.send_touch(ScrcpyControl.ACTION_MOVE, nx, ny2, w, h)
                            ctrl.send_touch(ScrcpyControl.ACTION_UP, nx, ny2, w, h)
                        elif t == "key":
                            _dispatch_key_control(ctrl, data["key"])
                    else:
                        # ── ADB shell fallback (control socket not connected) ──
                        serial = inst.serial
                        if t == "click":
                            adb_manager.tap(serial, nx, ny, w, h)
                        elif t == "drag_start":
                            drag_start_pos = (nx, ny)
                            drag_pos = (nx, ny)
                        elif t == "drag_move":
                            if data.get("scroll"):
                                prev = drag_pos or (nx, ny)
                                dx = abs(nx - prev[0]) * w
                                dy = abs(ny - prev[1]) * h
                                if dx + dy > 2:
                                    adb_manager.swipe(serial, prev[0], prev[1], nx, ny,
                                                      w, h, duration_ms=45)
                            else:
                                start = drag_start_pos or (nx, ny)
                                adb_manager.swipe(serial, start[0], start[1], nx, ny,
                                                  w, h, duration_ms=25)
                            drag_pos = (nx, ny)
                        elif t == "drag_end":
                            if data.get("scroll"):
                                prev = drag_pos or (nx, ny)
                                dx = abs(nx - prev[0]) * w
                                dy = abs(ny - prev[1]) * h
                                if dx + dy > 2:
                                    adb_manager.swipe(serial, prev[0], prev[1], nx, ny,
                                                      w, h, duration_ms=45)
                            drag_pos = None
                            drag_start_pos = None
                        elif t == "scroll":
                            adb_manager.scroll(serial, nx, ny, data.get("dy", 0), w, h)
                        elif t == "key":
                            adb_manager.send_key(serial, data["key"])
                except (KeyError, TypeError):
                    pass
        except WebSocketDisconnect:
            pass

    if os.path.isdir(CLIENT_DIR):
        app.mount("/static", StaticFiles(directory=CLIENT_DIR), name="static")

    return app
