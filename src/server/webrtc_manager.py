"""In-process aiortc WHEP server, replacing mediamtx for on-demand
per-instance WebRTC serving. See
docs/superpowers/specs/2026-08-28-mediamtx-aiortc-migration-design.md.

Each instance can have zero or more simultaneous viewers, each with its own
RTCPeerConnection + PassthroughH264Track. NAL units arrive from
ScrcpySession's persistent-loop OS thread (not the asyncio event loop this
manager runs on) via push_nalu_threadsafe(), which crosses that thread
boundary with call_soon_threadsafe -- asyncio.Queue (which
PassthroughH264Track wraps) is not safe to touch from any thread but the
loop's own.
"""
import asyncio
import uuid
from typing import Callable

from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription

from server.rtc_engine import PassthroughH264Track, _h264_codec_for_profile

# Matches mediamtx's webrtcHandshakeTimeout: 10s (see mediamtx_manager.py) --
# a PC that never reaches "connected" within this window is abandoned by the
# client (or the network) with no other signal telling us to clean it up, so
# we must not wait forever.
HANDSHAKE_TIMEOUT_SECONDS = 10.0


def _ice_servers_to_aiortc(servers: list[dict]) -> list[RTCIceServer]:
    """Convert server.ice_config.get_ice_servers()'s dict shape (built for
    the browser client's own RTCPeerConnection config) into aiortc's
    RTCIceServer dataclass, used for this process's own outbound/answering
    RTCPeerConnection.
    """
    result = []
    for s in servers:
        kwargs = {"urls": s["urls"]}
        if "username" in s:
            kwargs["username"] = s["username"]
        if "credential" in s:
            kwargs["credential"] = s["credential"]
        result.append(RTCIceServer(**kwargs))
    return result


class WebrtcManager:
    def __init__(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop
        self._tracks: dict[str, dict[str, PassthroughH264Track]] = {}
        self._pcs: dict[str, RTCPeerConnection] = {}
        # Handshake-deadline watchdog tasks, keyed by session_id. Cancelled
        # once the PC either connects (no longer needs watching) or is
        # closed by any other path (task itself would then be a no-op, but
        # cancelling avoids a dangling asyncio.sleep() outliving the session).
        self._handshake_deadlines: dict[str, asyncio.Task] = {}
        # Per-session on_disconnected callbacks (see create_session), fired
        # exactly once from close_session regardless of which of its three
        # callers (explicit DELETE, the reaper, or the handshake deadline)
        # actually triggers the close -- the one place whep_app.py needs to
        # know "a viewer just went away" to (re)schedule its close-grace
        # timer, since only DELETE used to carry that signal and no real
        # client ever sends one.
        self._on_disconnected: dict[str, Callable[[], None]] = {}

    def push_nalu_threadsafe(self, instance_name: str, nalu: bytes) -> None:
        self._loop.call_soon_threadsafe(self._push_nalu, instance_name, nalu)

    def _push_nalu(self, instance_name: str, nalu: bytes) -> None:
        for track in self._tracks.get(instance_name, {}).values():
            track.push_nalu(nalu)

    def viewer_count(self, instance_name: str) -> int:
        return len(self._tracks.get(instance_name, {}))

    async def create_session(
        self, instance_name: str, offer_sdp: str, profile_level_id: str,
        ice_servers: list[dict],
        on_connected: "Callable[[], None] | None" = None,
        on_disconnected: "Callable[[], None] | None" = None,
    ) -> tuple[str, str]:
        """Negotiate a new viewer session and register its PC for lifecycle
        management.

        `on_connected`, if given, is called (synchronously, no arguments)
        the first time this PC's connectionState reaches "connected" --
        used by whep_app.py to trigger a fresh IDR request now that the
        track is actually registered and can deliver it to this viewer
        (requesting one any earlier, e.g. when scrcpy video starts, gets
        discarded before this PC exists to receive it).

        `on_disconnected`, if given, is called (synchronously, no arguments)
        exactly once, from close_session -- whichever of the reaper, the
        handshake-deadline watchdog, or an explicit DELETE ends up calling
        it. Used by whep_app.py to (re)schedule its close-grace timer: that
        used to happen only from an explicit WHEP DELETE, but real clients
        never send one, so without this callback the reaper's cleanup would
        make viewer_count() accurate again without ever telling anyone the
        video should eventually stop.
        """
        config = RTCConfiguration(iceServers=_ice_servers_to_aiortc(ice_servers))
        pc = RTCPeerConnection(configuration=config)
        track = PassthroughH264Track()
        transceiver = pc.addTransceiver(track, direction="sendonly")
        transceiver.setCodecPreferences(_h264_codec_for_profile(profile_level_id))

        try:
            await pc.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type="offer"))
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
        except Exception:
            await pc.close()
            raise

        session_id = uuid.uuid4().hex
        self._tracks.setdefault(instance_name, {})[session_id] = track
        self._pcs[session_id] = pc
        if on_disconnected is not None:
            self._on_disconnected[session_id] = on_disconnected

        # Reap this PC once it leaves the connected world. DELETE
        # /{instance}/whep/{id} is the only other path that ever calls
        # close_session, but neither real client sends one: the browser
        # client's _probeLocalWhep abandons PCs by design (race-probe
        # pattern), and signaling_bridge.py never DELETEs either. Without
        # this handler, viewer_count() never returns to zero and on-demand
        # video never stops. Mirrors rtc_engine.py's run_engine(), which
        # uses the same "connectionstatechange" event to detect "connected"
        # for its IDR heartbeat (this manager's reaper is new -- run_engine
        # is a single long-lived CLI process with nothing else to reap).
        #
        # aiortc's RTCPeerConnection is a pyee AsyncIOEventEmitter: emit()
        # for an async handler schedules it via asyncio.ensure_future
        # (fire-and-forget, not a synchronous inline call), so this handler
        # always runs on this manager's own event loop -- calling
        # on_connected() directly (no call_soon needed) and awaiting
        # close_session() here are both safe. It's also safe for
        # close_session() to run twice for the same session_id (e.g. once
        # from an explicit DELETE, once more from the "closed" transition
        # that pc.close() itself triggers): the dict .pop(..., None) calls
        # below make the second call a no-op.
        @pc.on("connectionstatechange")
        async def _on_state_change():
            if pc.connectionState == "connected":
                self._cancel_handshake_deadline(session_id)
                if on_connected is not None:
                    on_connected()
            elif pc.connectionState in ("failed", "closed", "disconnected"):
                self._cancel_handshake_deadline(session_id)
                await self.close_session(instance_name, session_id)

        self._handshake_deadlines[session_id] = asyncio.create_task(
            self._close_if_handshake_stalls(instance_name, session_id, pc)
        )

        return session_id, pc.localDescription.sdp

    async def _close_if_handshake_stalls(
        self, instance_name: str, session_id: str, pc: RTCPeerConnection,
    ) -> None:
        """Force-close a PC that never reaches "connected" within
        HANDSHAKE_TIMEOUT_SECONDS -- e.g. a client that POSTed an offer but
        whose ICE never completes (firewalled network, abandoned tab).
        Without this, such a PC sits in _pcs/_tracks forever: it never
        reaches a state _on_state_change's reaper reacts to, and no DELETE
        is coming for it either.
        """
        try:
            await asyncio.sleep(HANDSHAKE_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return
        if pc.connectionState != "connected":
            await self.close_session(instance_name, session_id)

    def _cancel_handshake_deadline(self, session_id: str) -> None:
        task = self._handshake_deadlines.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()

    async def close_session(self, instance_name: str, session_id: str) -> None:
        self._cancel_handshake_deadline(session_id)
        tracks_for_instance = self._tracks.get(instance_name, {})
        tracks_for_instance.pop(session_id, None)
        if not tracks_for_instance:
            self._tracks.pop(instance_name, None)
        pc = self._pcs.pop(session_id, None)
        if pc is not None:
            await pc.close()
        # .pop(..., None) makes this naturally idempotent: a second
        # close_session() call for the same session_id (e.g. the "closed"
        # transition pc.close() itself triggers, re-entering here via
        # _on_state_change) finds nothing left to fire.
        on_disconnected = self._on_disconnected.pop(session_id, None)
        if on_disconnected is not None:
            on_disconnected()
