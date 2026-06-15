import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Literal

from config import CLIENT_DIR, QUALITY_MAP
from server.input_handler import handle_move, handle_scroll, handle_click_on_desktop, handle_key_on_desktop, handle_drag_start, handle_drag_move, handle_drag_end
from server.preview import capture_preview
from server.stream import CaptureState, FrameQueue, mjpeg_generator
from server.window_manager import focus_window


class SelectRequest(BaseModel):
    id: int


class QualityRequest(BaseModel):
    quality: Literal["low", "medium", "high"]


def _make_exception_handler(default_handler):
    def handler(loop, context):
        exc = context.get("exception")
        if isinstance(exc, ConnectionResetError):
            return  # client disconnected — expected on mobile
        if isinstance(exc, OSError) and getattr(exc, "winerror", None) == 10054:
            return  # WinError 10054 — same thing
        if default_handler:
            default_handler(loop, context)
        else:
            loop.default_exception_handler(context)
    return handler


def create_app(
    state: CaptureState,
    frame_queue: FrameQueue,
    available_windows: list[dict],
) -> FastAPI:
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
        return available_windows

    @app.post("/select")
    async def select_window(req: SelectRequest):
        match = next((w for w in available_windows if w["id"] == req.id), None)
        if match is None:
            raise HTTPException(status_code=404, detail="Window not found")
        state.set_hwnd(req.id)
        focus_window(req.id)
        return {"ok": True, "id": req.id}

    @app.get("/stream")
    async def stream():
        return StreamingResponse(
            mjpeg_generator(frame_queue),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @app.get("/window/{window_id}/preview")
    async def preview(window_id: int):
        match = next((w for w in available_windows if w["id"] == window_id), None)
        if match is None:
            raise HTTPException(status_code=404, detail="Window not found")
        jpeg = capture_preview(window_id)
        return Response(content=jpeg, media_type="image/jpeg")

    @app.post("/quality")
    async def set_quality(req: QualityRequest):
        state.set_quality(QUALITY_MAP[req.quality])
        return {"quality": req.quality}

    @app.websocket("/input")
    async def ws_input(websocket: WebSocket):
        await websocket.accept()
        try:
            while True:
                data = await websocket.receive_json()
                hwnd = state.active_hwnd
                if hwnd is None:
                    continue
                try:
                    t = data.get("type")
                    desktop = state.desktop
                    if t == "click":
                        handle_click_on_desktop(hwnd, data["x"], data["y"], desktop)
                    elif t == "move":
                        handle_move(hwnd, data["x"], data["y"])
                    elif t == "drag_start":
                        handle_drag_start(hwnd, data["x"], data["y"])
                    elif t == "drag_move":
                        handle_drag_move(hwnd, data["x"], data["y"])
                    elif t == "drag_end":
                        handle_drag_end(hwnd, data["x"], data["y"])
                    elif t == "scroll":
                        handle_scroll(hwnd, data.get("dx", 0), data.get("dy", 0))
                    elif t == "key":
                        handle_key_on_desktop(hwnd, data["key"], desktop)
                except (KeyError, TypeError):
                    pass
        except WebSocketDisconnect:
            pass

    # Serve static client files at /static/
    if os.path.isdir(CLIENT_DIR):
        app.mount("/static", StaticFiles(directory=CLIENT_DIR), name="static")

    return app
