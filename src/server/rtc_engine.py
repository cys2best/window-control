"""Python/aiortc replacement for engine.exe.

Wire protocol matches src/server/scrcpy_session.py exactly (see that
file's module docstring and _stream_loop method) and engine/src/
scrcpy_video.cpp's header comment:
  1. Connect TCP to 127.0.0.1:<port>.
  2. Read 1-byte dummy (sent by scrcpy-server immediately after accepting
     the video connection).
  3. Caller must now connect the control socket — scrcpy-server's
     accept() for control unblocks and it proceeds to send device_meta +
     codec header on THIS video socket.
  4. Read 64-byte device name (zero-padded UTF-8).
  5. Read 12-byte meta: codec_id (u32 BE) + width (u32 BE) + height (u32 BE).
  6. Frame loop: 12-byte header (u64 BE pts_flags + u32 BE size) + payload.
"""
import asyncio
import json as json_module
import socket
import struct
import time
from typing import AsyncIterator

import av
import websockets
from aiortc import MediaStreamTrack
from aiortc.mediastreams import VIDEO_TIME_BASE


class ScrcpyVideoClient:
    def __init__(self, port: int):
        self._port = port
        self._sock: socket.socket | None = None
        self.control_sock: socket.socket | None = None
        self._running = False
        self._read_task: asyncio.Task | None = None
        self._stopping = False
        # True only while read_frames() is actually suspended inside a
        # socket read (_recvall's `await loop.sock_recv(...)`), as opposed
        # to suspended at `yield payload` between frames (e.g. a caller that
        # only pulls one frame via __anext__() and never resumes the
        # generator). stop() must only cancel self._read_task while this is
        # True -- cancelling a task that's merely paused at `yield` would
        # cancel whatever unrelated code the task is actually running now.
        self._blocked_in_recv = False

    async def connect(self) -> None:
        loop = asyncio.get_event_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        await loop.run_in_executor(None, sock.connect, ("127.0.0.1", self._port))
        sock.setblocking(False)
        dummy = await loop.sock_recv(sock, 1)
        if len(dummy) != 1:
            raise ConnectionError("ScrcpyVideoClient: failed to read dummy byte after connect")
        self._sock = sock

    async def connect_control(self) -> None:
        loop = asyncio.get_event_loop()
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        await loop.run_in_executor(None, sock.connect, ("127.0.0.1", self._port))
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.control_sock = sock

    async def _recvall(self, n: int) -> bytes:
        loop = asyncio.get_event_loop()
        buf = b""
        while len(buf) < n:
            self._blocked_in_recv = True
            try:
                chunk = await loop.sock_recv(self._sock, n - len(buf))
            except (OSError, ValueError):
                # Socket closed, unregistered, or stop() called; treat as EOF
                raise ConnectionError("ScrcpyVideoClient: connection closed mid-read")
            finally:
                self._blocked_in_recv = False
            if not chunk:
                raise ConnectionError("ScrcpyVideoClient: connection closed mid-read")
            buf += chunk
        return buf

    async def read_handshake(self) -> tuple[str, int, int]:
        name_bytes = await self._recvall(64)
        device_name = name_bytes.rstrip(b"\x00").decode("utf-8", errors="replace")
        meta = await self._recvall(12)
        _codec_id, width, height = struct.unpack(">III", meta)
        return device_name, width, height

    async def read_frames(self) -> AsyncIterator[bytes]:
        # Capture the task we're running in so stop() can cancel exactly this
        # task to interrupt a blocked sock_recv() (see module docstring /
        # class-level design note in stop()). Closing the socket out from
        # under asyncio's selector does NOT reliably wake a pending
        # loop.sock_recv() — task cancellation is the only mechanism asyncio
        # guarantees will interrupt it.
        self._read_task = asyncio.current_task()
        self._running = True
        try:
            while self._running:
                try:
                    header = await self._recvall(12)
                except ConnectionError:
                    break
                except asyncio.CancelledError:
                    # Distinguish "stop() cancelled us on purpose" from a
                    # genuine external cancellation of this task. stop() sets
                    # _stopping=True before calling .cancel() on this exact
                    # task, so if both hold, this is our own clean-shutdown
                    # signal: end the generator quietly instead of
                    # propagating CancelledError to whoever is iterating us.
                    if self._stopping and asyncio.current_task() is self._read_task:
                        break
                    raise
                _pts_flags, size = struct.unpack(">QI", header)
                if size == 0:
                    payload = b""
                else:
                    payload = await self._recvall(size)
                yield payload
        finally:
            self._read_task = None

    def stop(self) -> None:
        self._running = False
        self._stopping = True
        # Cancel the task blocked inside read_frames()/_recvall() FIRST — this
        # is what actually interrupts a pending loop.sock_recv(). Socket
        # shutdown/close alone does not reliably wake a selector-registered
        # sock_recv() on this platform; it can sit forever with no error and
        # no completion. Task cancellation is asyncio's guaranteed mechanism.
        #
        # Only cancel while the task is genuinely suspended inside a socket
        # read. If it's merely paused at `yield payload` between frames (a
        # caller that hasn't resumed the generator), self._read_task may by
        # now refer to a task doing something else entirely (e.g. the
        # caller's own task, running unrelated code) -- cancelling it there
        # would be wrong. In that case _running=False alone is enough: the
        # generator's while-loop check ends it cleanly next time it resumes.
        if (
            self._read_task is not None
            and not self._read_task.done()
            and self._blocked_in_recv
        ):
            self._read_task.cancel()
        # Close sockets for resource cleanup only (releasing the fds) — no
        # longer relied upon to interrupt the blocked read.
        if self._sock:
            try:
                self._sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # socket already disconnected or not fully connected
            self._sock.close()
        if self.control_sock:
            try:
                self.control_sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # socket already disconnected or not fully connected
            self.control_sock.close()


class PassthroughH264Track(MediaStreamTrack):
    """Wraps already-encoded Annex-B H264 NALUs as av.Packet so aiortc's
    Encoder.pack() path repacketizes them into RTP with no decode/re-encode
    — the Python equivalent of engine/src/peer.cpp's H264RtpPacketizer use.
    """

    kind = "video"

    def __init__(self):
        super().__init__()
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=60)
        self._start_time = time.monotonic()

    def push_nalu(self, data: bytes) -> None:
        try:
            self._queue.put_nowait(data)
        except asyncio.QueueFull:
            pass  # drop rather than build unbounded latency on a slow consumer

    async def recv(self) -> av.Packet:
        data = await self._queue.get()
        packet = av.Packet(data)
        elapsed = time.monotonic() - self._start_time
        packet.pts = int(elapsed * VIDEO_TIME_BASE.denominator / VIDEO_TIME_BASE.numerator)
        packet.time_base = VIDEO_TIME_BASE
        return packet


class SignalingClient:
    def __init__(self, ws_url: str, session_id: str, role: str, token: str = ""):
        url = f"{ws_url}/?session={session_id}&role={role}"
        if token:
            url += f"&token={token}"
        self._url = url
        self._ws: websockets.WebSocketClientProtocol | None = None

    async def connect(self) -> None:
        self._ws = await websockets.connect(self._url)

    async def send(self, message: dict) -> None:
        await self._ws.send(json_module.dumps(message))

    async def recv(self) -> dict | None:
        raw = await self._ws.recv()
        try:
            parsed = json_module.loads(raw)
            if not isinstance(parsed, dict):
                return None
            return parsed
        except json_module.JSONDecodeError:
            return None

    async def close(self) -> None:
        if self._ws is not None:
            await self._ws.close()


from server.scrcpy_session import ScrcpyControl


def handle_input_message(
    control: ScrcpyControl, raw_json: str, screen_width: int, screen_height: int
) -> None:
    try:
        msg = json_module.loads(raw_json)
    except json_module.JSONDecodeError:
        return
    if not isinstance(msg, dict):
        return

    msg_type = msg.get("type", "")
    if msg_type in ("tap", "swipe"):
        action_name = msg.get("action", "down")
        action_code = {
            "down": ScrcpyControl.ACTION_DOWN,
            "up": ScrcpyControl.ACTION_UP,
            "move": ScrcpyControl.ACTION_MOVE,
        }.get(action_name, ScrcpyControl.ACTION_DOWN)
        nx = msg.get("nx", 0.0)
        ny = msg.get("ny", 0.0)
        control.send_touch(action_code, nx, ny, screen_width, screen_height)
    elif msg_type == "key":
        keycode = msg.get("keycode", 0)
        if keycode != 0:
            control.send_keycode(keycode)


import re
import sys

from aiortc import (
    RTCConfiguration,
    RTCIceCandidate,
    RTCIceServer,
    RTCPeerConnection,
    RTCRtpSender,
    RTCSessionDescription,
)

_ICE_URL_WITH_CREDENTIALS_RE = re.compile(r"^(turns?):([^:]+):([^@]+)@(.+)$")


def _h264_only_video_codecs() -> list:
    """Return only the H264 entries from aiortc's video codec capabilities,
    in the order aiortc reports them.

    Verified against the installed aiortc version (RTCRtpSender.getCapabilities
    ("video") returns VP8, video/rtx, and two H264 entries with different
    profile-level-id values -- 42001f and 42e01f). By default aiortc's
    createOffer() offers every one of these codecs (VP8 included) and lets
    the remote peer pick; a real browser test picked VP8, and
    PassthroughH264Track only ever produces raw H264 Annex-B NALUs wrapped
    in av.Packet, so a VP8-negotiated connection silently sends garbage
    (H264 bytes interpreted as VP8) and nothing decodes.

    Keeping both H264 profile-level-id entries (rather than picking one) is
    deliberate: scrcpy/MediaCodec's actual encoder profile varies by device,
    and offering both still excludes VP8/rtx entirely, which is the actual
    fix -- the browser must still land on H264, just possibly a different
    profile-level-id line depending on what it/aiortc negotiate between
    the two.
    """
    caps = RTCRtpSender.getCapabilities("video")
    return [codec for codec in caps.codecs if codec.mimeType == "video/H264"]


def _parse_ice_url(ice_url: str) -> RTCIceServer:
    """Parse a combined `turn:user:pass@host:port`-style CLI argument into
    an aiortc RTCIceServer.

    aiortc.RTCIceServer (a plain dataclass) does NOT accept embedded
    credentials in `urls` -- `urls` must be the bare `turn:host:port` (or
    `stun:host:port`) string, with `username`/`credential` passed as their
    own fields. Passing the combined form directly as `urls` crashes deep
    inside aiortc.rtcicetransport.parse_stun_turn_uri() with
    `ValueError: malformed uri` the first time a track/DTLS transport is
    created. This mirrors engine/test/test_page.html's JS-side
    parseIceServer(), which solves the identical problem for the browser.
    """
    match = _ICE_URL_WITH_CREDENTIALS_RE.match(ice_url)
    if match is None:
        return RTCIceServer(urls=ice_url)
    scheme, username, credential, hostpart = match.groups()
    return RTCIceServer(urls=f"{scheme}:{hostpart}", username=username, credential=credential)


def _parse_ice_candidate(candidate_str: str, mid: str) -> RTCIceCandidate | None:
    """Parse a candidate line of the form emitted by both this engine's
    peer and the C++ engine's rtc::Candidate stringification, e.g.:
    'candidate:1 1 UDP 2130706431 192.168.1.5 54400 typ host'

    NOTE: aiortc 1.15.0's `aiortc.sdp` module exposes `candidate_from_sdp`
    (parse) and `candidate_to_sdp` (serialize) as separate module-level
    functions -- verified via `help(candidate_from_sdp)` against the
    installed version. There is no `RTCIceCandidate.to_sdp()` instance
    method in this version (confirmed absent via introspection), so any
    serialization of a *local* candidate back to SDP text must use
    `candidate_to_sdp(candidate)`, not `candidate.to_sdp()`.
    """
    from aiortc.sdp import candidate_from_sdp

    try:
        cand = candidate_from_sdp(candidate_str.removeprefix("candidate:"))
        cand.sdpMid = mid
        return cand
    except (ValueError, AssertionError, IndexError):
        return None


async def run_engine(scrcpy_port: int, signaling_url: str, session_id: str, ice_url: str) -> None:
    video = ScrcpyVideoClient(scrcpy_port)
    await video.connect()
    await video.connect_control()
    device_name, width, height = await video.read_handshake()
    print(f"scrcpy handshake: device={device_name} {width}x{height}", flush=True)

    control = ScrcpyControl(scrcpy_port, device_name)
    control._sock = video.control_sock  # already connected by connect_control()

    config = RTCConfiguration(iceServers=[_parse_ice_url(ice_url)])
    pc = RTCPeerConnection(configuration=config)

    track = PassthroughH264Track()
    # Use addTransceiver() instead of addTrack() so we get the
    # RTCRtpTransceiver directly (addTrack() returns only the RTCRtpSender).
    # Constrain the offer to H264-only: aiortc's createOffer() otherwise
    # offers every codec it supports (VP8 included) and lets the remote
    # peer choose -- real E2E testing against a browser showed it picking
    # VP8, which PassthroughH264Track's raw H264 Annex-B NALUs cannot
    # satisfy (the browser decodes H264 bytes as VP8 and nothing renders).
    transceiver = pc.addTransceiver(track, direction="sendrecv")
    transceiver.setCodecPreferences(_h264_only_video_codecs())

    input_channel = pc.createDataChannel("input")

    @input_channel.on("message")
    def on_input_message(message):
        if isinstance(message, str):
            handle_input_message(control, message, width, height)

    signaling = SignalingClient(signaling_url, session_id, "engine", token="")
    await signaling.connect()

    # NOTE: aiortc 1.15.0's RTCPeerConnection does not emit an "icecandidate"
    # event (verified by inspecting its emit(...) call sites -- it only
    # emits track/datachannel/signalingstatechange/connectionstatechange/
    # iceconnectionstatechange/icegatheringstatechange). This is not an
    # oversight to work around: aiortc's setLocalDescription() internally
    # awaits full ICE gathering (self.__gather()) before returning, so by
    # the time we call pc.setLocalDescription(offer) below, ICE gathering
    # is already complete and every local candidate is embedded directly
    # in pc.localDescription.sdp. There is therefore nothing to trickle
    # out locally -- the offer's SDP already carries the full candidate
    # set. Only remote (browser-side) trickled candidates arriving via
    # signaling need handling, which signaling_loop()'s "candidate" branch
    # below does via _parse_ice_candidate()/pc.addIceCandidate().

    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    await signaling.send({"type": pc.localDescription.type, "sdp": pc.localDescription.sdp})
    print(f"[debug] sent {pc.localDescription.type}", flush=True)

    peer_connected = asyncio.Event()

    @pc.on("connectionstatechange")
    async def on_connectionstatechange():
        print(f"[peer] state: {pc.connectionState}", flush=True)
        if pc.connectionState == "connected":
            peer_connected.set()

    async def signaling_loop():
        while True:
            msg = await signaling.recv()
            if msg is None:
                continue
            msg_type = msg.get("type", "")
            if msg_type == "answer":
                sdp = msg.get("sdp", "")
                if sdp:
                    await pc.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="answer"))
            elif msg_type == "candidate":
                candidate_str = msg.get("candidate", "")
                mid = msg.get("mid", "")
                if candidate_str:
                    cand = _parse_ice_candidate(candidate_str, mid)
                    if cand is not None:
                        await pc.addIceCandidate(cand)

    async def video_pump_loop():
        async for nalu in video.read_frames():
            track.push_nalu(nalu)

    async def idr_heartbeat_loop():
        await peer_connected.wait()
        print("[debug] peer connected, requesting IDR", flush=True)
        control.request_idr()
        while True:
            await asyncio.sleep(2.0)
            control.request_idr()

    print("Streaming started. Press Ctrl+C to stop.", flush=True)
    try:
        await asyncio.gather(signaling_loop(), video_pump_loop(), idr_heartbeat_loop())
    except asyncio.CancelledError:
        pass
    finally:
        video.stop()
        await pc.close()
        await signaling.close()
        print("Stopped.")


def main() -> None:
    if len(sys.argv) < 5:
        print(
            "Usage: python -m server.rtc_engine <scrcpy_port> <signaling_ws_url> "
            "<session_id> <stun_turn_url>\n"
            "Example: python -m server.rtc_engine 27183 ws://VPS_IP:8443 "
            "poc-session-1 stun:VPS_IP:3478",
            file=sys.stderr,
        )
        sys.exit(1)

    scrcpy_port = int(sys.argv[1])
    signaling_url = sys.argv[2]
    session_id = sys.argv[3]
    ice_url = sys.argv[4]

    try:
        asyncio.run(run_engine(scrcpy_port, signaling_url, session_id, ice_url))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
