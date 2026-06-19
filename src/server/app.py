import io
import os
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Literal

from config import CLIENT_DIR, QUALITY_MAP, WHEP_PORT
from server.stream import CaptureState, FrameQueue, mjpeg_generator
from server import adb_manager
from server.instance_manager import InstanceManager
from server.tailscale import get_best_ip


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


def create_app(state: CaptureState, frame_queue: FrameQueue,
               instance_manager: InstanceManager) -> FastAPI:
    import asyncio
    app = FastAPI()

    @app.on_event("startup")
    async def _startup():
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(_make_exception_handler(loop.get_exception_handler()))
        # Discover LDPlayer instances on startup
        import threading
        threading.Thread(target=instance_manager.refresh, daemon=True).start()

    # ── Static / index ───────────────────────────────────────────────────────

    @app.get("/")
    async def index():
        html_path = os.path.join(CLIENT_DIR, "index.html")
        if os.path.exists(html_path):
            return HTMLResponse(Path(html_path).read_text())
        return HTMLResponse("<h1>Client not found</h1>", status_code=500)

    # ── Instance management ──────────────────────────────────────────────────

    @app.get("/instances")
    async def get_instances():
        return instance_manager.list_instances()

    @app.post("/instances/{instance_id}/select")
    async def select_instance(instance_id: str, request: Request):
        """Switch active stream. instance_id is the ADB serial (no prefix)."""
        ok = instance_manager.select(instance_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Instance not found")
        inst = instance_manager.active
        if inst is None:
            raise HTTPException(status_code=404, detail="Instance disappeared")

        host = get_best_ip() or request.client.host
        whep_url = f"http://{host}:{WHEP_PORT}/{inst.name}/whep"
        return {
            "ok": True,
            "id": inst.id,
            "serial": inst.serial,
            "name": inst.name,
            "w": inst.w,
            "h": inst.h,
            "whep_url": whep_url,
        }

    @app.get("/instances/{instance_id}/preview")
    async def instance_preview(instance_id: str):
        from PIL import Image
        adb = adb_manager._find_adb()
        if not adb:
            raise HTTPException(status_code=503, detail="adb not found")
        nw = adb_manager._no_window_flags()
        try:
            png = subprocess.check_output(
                [adb, "-s", instance_id, "exec-out", "screencap -p"],
                timeout=5, **nw
            )
            img = Image.open(io.BytesIO(png))
            img.thumbnail((200, 120))
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=60)
            return Response(content=buf.getvalue(), media_type="image/jpeg")
        except Exception:
            raise HTTPException(status_code=503, detail="Preview capture failed")

    # ── Legacy /windows + /select (kept for backward compat) ────────────────

    @app.get("/windows")
    async def get_windows():
        return instance_manager.list_instances()

    @app.post("/select")
    async def select_window(req: SelectRequest, request: Request):
        if not req.id.startswith("adb:"):
            raise HTTPException(status_code=400, detail="Invalid id — must be adb:SERIAL")
        serial = req.id[4:]
        ok = instance_manager.select(serial)
        if not ok:
            # Instance may not be discovered yet — try refresh
            instance_manager.refresh()
            ok = instance_manager.select(serial)
        if not ok:
            raise HTTPException(status_code=404, detail="Instance not found")
        inst = instance_manager.active
        if inst is None:
            raise HTTPException(status_code=404, detail="Instance disappeared")

        host = get_best_ip() or request.client.host
        whep_url = f"http://{host}:{WHEP_PORT}/{inst.name}/whep"
        return {"ok": True, "id": req.id, "w": inst.w, "h": inst.h,
                "whep_url": whep_url}

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
        from PIL import Image
        adb = adb_manager._find_adb()
        if not adb:
            raise HTTPException(status_code=503, detail="adb not found")
        nw = adb_manager._no_window_flags()
        try:
            png = subprocess.check_output(
                [adb, "-s", window_id, "exec-out", "screencap -p"],
                timeout=5, **nw
            )
            img = Image.open(io.BytesIO(png))
            img.thumbnail((200, 120))
            buf = io.BytesIO()
            img.convert("RGB").save(buf, format="JPEG", quality=60)
            return Response(content=buf.getvalue(), media_type="image/jpeg")
        except Exception:
            raise HTTPException(status_code=503, detail="Preview capture failed")

    # ── Quality ──────────────────────────────────────────────────────────────

    @app.post("/quality")
    async def set_quality(req: QualityRequest):
        state.set_quality(QUALITY_MAP[req.quality])
        return {"quality": req.quality}

    # ── WebSocket input ──────────────────────────────────────────────────────

    @app.websocket("/input")
    async def ws_input(websocket: WebSocket):
        await websocket.accept()
        import asyncio as _asyncio

        async def _ping():
            while True:
                await _asyncio.sleep(20)
                try:
                    await websocket.send_text('{"type":"ping"}')
                except Exception:
                    return
        _asyncio.create_task(_ping())

        drag_pos: tuple | None = None
        try:
            while True:
                data = await websocket.receive_json()
                inst = instance_manager.active
                if inst is None:
                    continue
                try:
                    t = data.get("type")
                    nx, ny = data.get("x", 0.5), data.get("y", 0.5)
                    w, h = inst.w, inst.h
                    serial = inst.serial
                    if t == "click":
                        adb_manager.tap(serial, nx, ny, w, h)
                    elif t == "drag_start":
                        drag_pos = (nx, ny)
                    elif t == "drag_move":
                        prev = drag_pos or (nx, ny)
                        dx = abs(nx - prev[0]) * w
                        dy = abs(ny - prev[1]) * h
                        if dx + dy > 2:
                            dur = 200 if data.get("scroll") else 30
                            adb_manager.swipe(serial, prev[0], prev[1], nx, ny,
                                              w, h, duration_ms=dur)
                            drag_pos = (nx, ny)
                    elif t == "drag_end":
                        prev = drag_pos or (nx, ny)
                        dx = abs(nx - prev[0]) * w
                        dy = abs(ny - prev[1]) * h
                        if dx + dy > 2:
                            dur = 200 if data.get("scroll") else 30
                            adb_manager.swipe(serial, prev[0], prev[1], nx, ny,
                                              w, h, duration_ms=dur)
                        drag_pos = None
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
