import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Literal

from config import CLIENT_DIR, QUALITY_MAP
from server.stream import CaptureState, FrameQueue, mjpeg_generator
from server import adb_manager


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


def create_app(state: CaptureState, frame_queue: FrameQueue) -> FastAPI:
    import asyncio
    app = FastAPI()

    @app.on_event("startup")
    async def _suppress_connection_reset():
        loop = asyncio.get_event_loop()
        loop.set_exception_handler(_make_exception_handler(loop.get_exception_handler()))

    @app.get("/")
    async def index():
        html_path = os.path.join(CLIENT_DIR, "index.html")
        if os.path.exists(html_path):
            return HTMLResponse(Path(html_path).read_text())
        return HTMLResponse("<h1>Client not found</h1>", status_code=500)

    @app.get("/windows")
    async def get_windows():
        return adb_manager.list_vms()

    @app.post("/select")
    async def select_window(req: SelectRequest):
        if not req.id.startswith("adb:"):
            raise HTTPException(status_code=400, detail="Invalid id — must be adb:SERIAL")
        serial = req.id[4:]
        # Find ldplayer_index from cached VM list
        vms = adb_manager.list_vms()
        ldplayer_index = next((v.get("ldplayer_index", 0) for v in vms if v["id"] == req.id), 0)
        w, h = adb_manager.get_screen_size(serial)
        session = adb_manager.AdbSession(serial, w, h, fps=15, ldplayer_index=ldplayer_index)
        if not session.start():
            raise HTTPException(status_code=503, detail="Could not start ADB session")
        state.set_adb_session(session)
        # Maximize LDPlayer window on Windows so user sees the instance
        import threading as _t
        _t.Thread(target=adb_manager.maximize_ldplayer_window,
                  args=(ldplayer_index,), daemon=True).start()
        return {"ok": True, "id": req.id, "w": w, "h": h}

    @app.get("/stream")
    async def stream():
        return StreamingResponse(
            mjpeg_generator(frame_queue, state),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @app.get("/stats")
    async def stats():
        """Lightweight FPS counter — client polls every second."""
        count = state.frames_served
        state.frames_served = 0
        session = state.adb_session
        return {"frames": count, "active": session is not None}

    @app.post("/reconnect")
    async def reconnect():
        """Re-start the ADB capture pipeline for the current session."""
        session = state.adb_session
        if session is None:
            raise HTTPException(status_code=404, detail="No active session")
        session.stop()
        ok = session.start()
        if not ok:
            raise HTTPException(status_code=503, detail="Could not restart session")
        return {"ok": True}

    @app.get("/window/{window_id}/preview")
    async def preview(window_id: str):
        # window_id is the serial (adb: prefix already stripped by JS)
        import subprocess, io
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

    @app.post("/quality")
    async def set_quality(req: QualityRequest):
        state.set_quality(QUALITY_MAP[req.quality])
        return {"quality": req.quality}

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

        try:
            while True:
                data = await websocket.receive_json()
                session = state.adb_session
                if session is None:
                    continue
                try:
                    t = data.get("type")
                    nx, ny = data.get("x", 0.5), data.get("y", 0.5)
                    w, h = session.w, session.h
                    if t == "click":
                        adb_manager.tap(session.serial, nx, ny, w, h)
                    elif t == "drag_start":
                        websocket._drag_pos = (nx, ny)
                    elif t == "drag_move":
                        prev = getattr(websocket, "_drag_pos", (nx, ny))
                        dx = abs(nx - prev[0]) * w
                        dy = abs(ny - prev[1]) * h
                        if dx + dy > 2:
                            # Scroll needs 200ms+ so Android recognises as scroll not fling.
                            dur = 200 if data.get("scroll") else 30
                            adb_manager.swipe(session.serial,
                                              prev[0], prev[1], nx, ny, w, h,
                                              duration_ms=dur)
                            websocket._drag_pos = (nx, ny)
                    elif t == "drag_end":
                        prev = getattr(websocket, "_drag_pos", (nx, ny))
                        dx = abs(nx - prev[0]) * w
                        dy = abs(ny - prev[1]) * h
                        if dx + dy > 2:
                            dur = 200 if data.get("scroll") else 30
                            adb_manager.swipe(session.serial,
                                              prev[0], prev[1], nx, ny, w, h,
                                              duration_ms=dur)
                        websocket._drag_pos = None
                    elif t == "scroll":
                        adb_manager.scroll(session.serial, nx, ny, data.get("dy", 0), w, h)
                    elif t == "key":
                        adb_manager.send_key(session.serial, data["key"])
                except (KeyError, TypeError):
                    pass
        except WebSocketDisconnect:
            pass

    if os.path.isdir(CLIENT_DIR):
        app.mount("/static", StaticFiles(directory=CLIENT_DIR), name="static")

    return app
