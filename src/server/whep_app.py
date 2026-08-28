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
from fastapi.middleware.cors import CORSMiddleware

from config import AIORTC_PROFILE_LEVEL_ID, STUN_PORT
from server.ice_config import get_ice_servers
from server.instance_manager import InstanceManager
from server.tailscale import get_best_ip
from server.webrtc_manager import WebrtcManager

log = logging.getLogger(__name__)

_CLOSE_GRACE_SECONDS = 45.0  # matches mediamtx's runOnDemandCloseAfter today


def _lan_ice_servers(host: str, is_public_path: bool) -> list[dict]:
    """Build the ICE server list for the Python-side aiortc RTCPeerConnection
    this endpoint negotiates -- NOT the same list app.py's /select hands the
    browser client (that one is deliberately the public list; the browser
    itself decides which candidates to try).

    This endpoint serves both a direct LAN/Tailscale client (this
    migration's actual Phase 1 scope) and the public path via
    signaling_bridge.py, which always POSTs from 127.0.0.1 (see Finding #6).
    These two cases need genuinely different ice_servers, not a merged
    list -- an earlier attempt at merging (Tailscale STUN as primary, with
    TURN layered on top for the loopback case) still reproduced coturn's
    "403 Forbidden IP" live: this PC's own host candidate ends up being its
    Tailscale IP (100.64.0.0/10, RFC 6598 CGNAT space) either way once that
    STUN entry is present, and coturn denies relaying to any peer address in
    that range by default (it's the same policy that blocks relaying to
    127.0.0.0/8, 169.254.0.0/16, 192.168.0.0/16, etc. -- a built-in
    SSRF-style guard, not something misconfigured on the VPS). So:

    - Public path (signaling_bridge.py's loopback POST): use
      get_ice_servers() untouched -- the real public STUN discovers this
      PC's actual internet-facing srflx candidate (not its Tailscale IP),
      and TURN is a legitimate relay fallback for genuinely NAT'd public
      peers, matching ice_config.py's own stated scope.
    - Direct LAN/Tailscale peer: Tailscale-bound STUN only
      (stun_server.py via STUN_PORT, matching what app.py's /select already
      hands the browser as stun_url), no TURN -- a same-Tailnet peer never
      needs a relay, and offering TURN here is what produced the 403 in the
      first place.
    """
    if is_public_path:
        return get_ice_servers()
    return [{"urls": f"stun:{host}:{STUN_PORT}"}]


def create_whep_app(instance_manager: InstanceManager, webrtc: WebrtcManager) -> FastAPI:
    app = FastAPI()
    # This server runs on WHEP_PORT (8889), cross-origin from the browser
    # client served on PORT (8080). Its POST body is application/sdp, which
    # is not CORS-safelisted, so browsers send an OPTIONS preflight first --
    # with no route/headers for it, the browser client could not reach this
    # endpoint at all. mediamtx's default webrtcAllowOrigin: '*' covered
    # this invisibly; allow_origins=["*"] here matches that existing
    # posture exactly, not a new exposure. expose_headers=["Location"] is
    # required separately: Location is not CORS-safelisted as a *response*
    # header by default, so without it a browser-side client can read the
    # POST's 201 response but not its Location header, and so wouldn't know
    # where to send its eventual DELETE.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=["Location"],
    )
    _grace_tasks: dict[str, asyncio.Task] = {}
    # Per-instance locks serializing start_video/stop_video decisions. See
    # _close_after_grace's docstring for the TOCTOU race this closes: a
    # grace-period stop_video dispatched to a thread and a fresh POST's
    # start_video must never be allowed to run concurrently for the same
    # instance, or whichever finishes last silently wins regardless of which
    # one is actually correct.
    _video_locks: dict[str, asyncio.Lock] = {}

    def _video_lock(instance_name: str) -> asyncio.Lock:
        lock = _video_locks.get(instance_name)
        if lock is None:
            lock = asyncio.Lock()
            _video_locks[instance_name] = lock
        return lock

    def _cancel_pending_grace(instance_name: str) -> None:
        task = _grace_tasks.pop(instance_name, None)
        if task is not None and not task.done():
            task.cancel()

    def _schedule_grace_if_idle(instance_name: str) -> None:
        """(Re)schedule the close-grace timer if no viewer is currently
        registered. The one place that decides this -- called from
        WebrtcManager's on_disconnected callback (fires for every session
        teardown: explicit DELETE, the connectionstatechange reaper, and the
        handshake-deadline watchdog alike, see webrtc_manager.py) as well as
        from whep_offer's own except-block (create_session can fail before a
        session/callback ever gets registered, so that path can't rely on
        the callback and must schedule for itself).
        """
        if webrtc.viewer_count(instance_name) == 0:
            _grace_tasks[instance_name] = asyncio.create_task(
                _close_after_grace(instance_name)
            )

    async def _close_after_grace(instance_name: str) -> None:
        try:
            await asyncio.sleep(_CLOSE_GRACE_SECONDS)
        except asyncio.CancelledError:
            return
        # From here on, this task is committed to the check-and-maybe-stop
        # below and must not be allowed to abandon it partway through.
        # asyncio.shield() protects that: without it, a cancellation racing
        # in right now (e.g. a fresh POST's _cancel_pending_grace, which
        # cannot tell whether we're still safely asleep or already past this
        # point) would make the `await` inside _stop_if_still_idle raise
        # CancelledError immediately and unwind out of its `async with
        # _video_lock(...)` block -- releasing the lock WHILE
        # instance_manager.stop_video's real call keeps running unsupervised
        # in its own thread (cancelling the asyncio Task wrapping
        # asyncio.to_thread does not stop a concurrent.futures.Future that
        # has already started running; that cancel() is a documented no-op
        # once RUNNING). That premature release is exactly the TOCTOU this
        # lock exists to close: a concurrent start_video could then race
        # ahead of the still-in-flight stop_video and lose. Shielding keeps
        # _stop_if_still_idle running to genuine completion -- lock acquired
        # for its full real duration -- no matter what happens to us.
        await asyncio.shield(_stop_if_still_idle(instance_name))

    async def _stop_if_still_idle(instance_name: str) -> None:
        async with _video_lock(instance_name):
            if webrtc.viewer_count(instance_name) == 0:
                await asyncio.to_thread(instance_manager.stop_video, instance_name)

    @app.post("/{instance_name}/whep")
    async def whep_offer(instance_name: str, request: Request):
        inst = instance_manager.get_by_name(instance_name)
        if inst is None:
            raise HTTPException(status_code=404, detail="Instance not found")

        _cancel_pending_grace(instance_name)

        # Read outside the lock: request.body() can stall arbitrarily long
        # on a slow/stalled client, and get_best_ip() shells out
        # (server/tailscale.py's ipconfig call) -- neither belongs on the
        # media event loop, let alone serializing every other viewer's
        # join/teardown behind it. Loopback means signaling_bridge.py's own
        # POST (see _lan_ice_servers' docstring) -- the only path that
        # should ever get TURN offered to this process's own PC.
        offer_sdp = (await request.body()).decode("utf-8")
        is_public_path = request.client is not None and request.client.host in ("127.0.0.1", "::1")
        host = await asyncio.to_thread(get_best_ip) or (request.client.host if request.client else "")

        # Held across the whole start_video decision AND create_session
        # negotiation (not just the start_video dispatch) -- viewer_count()
        # only becomes accurate once create_session registers the new
        # track/PC, so releasing the lock any earlier would let a
        # concurrently-running _stop_if_still_idle (same lock) re-check
        # viewer_count() in the gap and wrongly still see 0, tearing down
        # the video this request is in the middle of standing back up.
        async with _video_lock(instance_name):
            try:
                if not inst.session.video_active:
                    started = await asyncio.to_thread(instance_manager.start_video, instance_name)
                    if not started:
                        raise HTTPException(status_code=503, detail="Could not start video")

                # request_idr() is threaded through as on_connected rather
                # than called here: start_video's own IDR request
                # (scrcpy_session.py's start_video_aiortc) fires before this
                # PC/track exists, so that keyframe is discarded before any
                # viewer can receive it. The only other IDR source is the 8s
                # heartbeat, so without this a first-time viewer (or a
                # second viewer joining an already-active instance) can see
                # up to 8s of black screen.
                session_id, answer_sdp = await webrtc.create_session(
                    instance_name, offer_sdp, AIORTC_PROFILE_LEVEL_ID,
                    _lan_ice_servers(host, is_public_path=is_public_path),
                    on_connected=inst.session.control.request_idr,
                    on_disconnected=lambda n=instance_name: _schedule_grace_if_idle(n),
                )
            except Exception:
                # The grace timer we just cancelled above was covering this
                # instance's zero-viewer video; if start_video/create_session
                # then fails, that cancellation must not stand uncorrected --
                # otherwise on-demand video is orphaned running with no
                # viewer and no timer left to ever stop it. create_session
                # failing here means no session/on_disconnected callback was
                # ever registered for it, so this path can't rely on that
                # callback and must schedule for itself.
                _schedule_grace_if_idle(instance_name)
                raise

        return Response(
            content=answer_sdp,
            media_type="application/sdp",
            status_code=201,
            headers={"Location": f"/{instance_name}/whep/{session_id}"},
        )

    @app.delete("/{instance_name}/whep/{session_id}")
    async def whep_delete(instance_name: str, session_id: str):
        # No manual "schedule grace if idle" here: close_session() fires the
        # on_disconnected callback whep_offer registered above (mirrors
        # exactly what the reaper/handshake-deadline paths already rely on),
        # so this stays the single source of truth instead of a third
        # duplicate copy of the same check.
        await webrtc.close_session(instance_name, session_id)
        return Response(status_code=200)

    return app
