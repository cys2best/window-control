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

from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription

from server.rtc_engine import PassthroughH264Track, _h264_codec_for_profile


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
    ) -> tuple[str, str]:
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
        return session_id, pc.localDescription.sdp

    async def close_session(self, instance_name: str, session_id: str) -> None:
        tracks_for_instance = self._tracks.get(instance_name, {})
        tracks_for_instance.pop(session_id, None)
        if not tracks_for_instance:
            self._tracks.pop(instance_name, None)
        pc = self._pcs.pop(session_id, None)
        if pc is not None:
            await pc.close()
