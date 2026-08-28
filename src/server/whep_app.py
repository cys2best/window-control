"""HTTP WHEP contract for the aiortc backend -- the in-process replacement
for mediamtx's WHEP endpoint. See
docs/superpowers/specs/2026-08-28-mediamtx-aiortc-migration-design.md.

The offer/answer flow needs no ICE trickle/PATCH support: aiortc's
setLocalDescription() awaits full ICE gathering before returning (so the
answer SDP already carries every local candidate), and the mobile client
already waits for iceGatheringState === "complete" before POSTing its own
offer -- confirmed in mobile/src/webrtc/whep.ts. This mirrors exactly what
mediamtx's own WHEP endpoint already provided, so the client needs zero
changes.
"""
import asyncio
import logging

from fastapi import FastAPI, HTTPException, Request, Response

from config import AIORTC_PROFILE_LEVEL_ID
from server.ice_config import get_ice_servers
from server.instance_manager import InstanceManager
from server.webrtc_manager import WebrtcManager

log = logging.getLogger(__name__)

_CLOSE_GRACE_SECONDS = 45.0  # matches mediamtx's runOnDemandCloseAfter today


def create_whep_app(instance_manager: InstanceManager, webrtc: WebrtcManager) -> FastAPI:
    app = FastAPI()
    _grace_tasks: dict[str, asyncio.Task] = {}

    def _cancel_pending_grace(instance_name: str) -> None:
        task = _grace_tasks.pop(instance_name, None)
        if task is not None and not task.done():
            task.cancel()

    async def _close_after_grace(instance_name: str) -> None:
        try:
            await asyncio.sleep(_CLOSE_GRACE_SECONDS)
        except asyncio.CancelledError:
            return
        if webrtc.viewer_count(instance_name) == 0:
            await asyncio.to_thread(instance_manager.stop_video, instance_name)

    @app.post("/{instance_name}/whep")
    async def whep_offer(instance_name: str, request: Request):
        inst = instance_manager.get_by_name(instance_name)
        if inst is None:
            raise HTTPException(status_code=404, detail="Instance not found")

        _cancel_pending_grace(instance_name)

        try:
            if not inst.session.video_active:
                started = await asyncio.to_thread(instance_manager.start_video, instance_name)
                if not started:
                    raise HTTPException(status_code=503, detail="Could not start video")

            offer_sdp = (await request.body()).decode("utf-8")
            session_id, answer_sdp = await webrtc.create_session(
                instance_name, offer_sdp, AIORTC_PROFILE_LEVEL_ID, get_ice_servers(),
            )
        except Exception:
            # The grace timer we just cancelled above was covering this
            # instance's zero-viewer video; if start_video/create_session
            # then fails, that cancellation must not stand uncorrected --
            # otherwise on-demand video is orphaned running with no viewer
            # and no timer left to ever stop it.
            if webrtc.viewer_count(instance_name) == 0:
                _grace_tasks[instance_name] = asyncio.create_task(
                    _close_after_grace(instance_name)
                )
            raise

        return Response(
            content=answer_sdp,
            media_type="application/sdp",
            status_code=201,
            headers={"Location": f"/{instance_name}/whep/{session_id}"},
        )

    @app.delete("/{instance_name}/whep/{session_id}")
    async def whep_delete(instance_name: str, session_id: str):
        await webrtc.close_session(instance_name, session_id)
        if webrtc.viewer_count(instance_name) == 0:
            _grace_tasks[instance_name] = asyncio.create_task(
                _close_after_grace(instance_name)
            )
        return Response(status_code=200)

    return app
