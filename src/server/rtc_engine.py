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


def extract_profile_level_id(sps_nalu: bytes) -> str | None:
    """Extract the 6-hex-char H264 profile-level-id from a raw Annex-B SPS
    NALU (start code + nal_type==7 header byte + profile_idc/constraint_flags
    /level_idc bytes).

    Expects the Annex-B 4-byte start code (00 00 00 01) followed by the NAL
    header byte, mirroring the exact frame format seen live over the scrcpy
    video socket and already exercised by this test suite's fixtures (e.g.
    b"\\x00\\x00\\x00\\x01\\x67...").  For a NALU starting `00 00 00 01 67 XX
    YY ZZ ...`: `67` is the NAL header byte (forbidden_zero_bit=0,
    nal_ref_idc=3, nal_type=7=SPS), and `XX YY ZZ` are profile_idc,
    constraint_flags, and level_idc respectively -- exactly the three bytes
    that make up the SDP fmtp `profile-level-id` value when formatted as
    hex. Confirmed against a real captured SPS from live E2E debug logging:
    `0000000167 42c0298d680b435f964200` (spaces removed before parsing)
    decodes to profile_idc=0x42, constraint_flags=0xc0, level_idc=0x29 ->
    "42c029".

    Returns None if `sps_nalu` is too short to contain a start code + NAL
    header + 3 profile bytes, or if the NAL header byte's low 5 bits don't
    indicate nal_type==7 (SPS). Does not attempt to locate the start code
    if it isn't at offset 0 (real frames from ScrcpyVideoClient.read_frames()
    always begin exactly at the start code with no leading garbage).
    """
    if len(sps_nalu) < 8:
        return None
    if sps_nalu[:3] == b"\x00\x00\x01":
        header_offset = 3
    elif sps_nalu[:4] == b"\x00\x00\x00\x01":
        header_offset = 4
    else:
        return None
    if len(sps_nalu) < header_offset + 4:
        return None
    header_byte = sps_nalu[header_offset]
    nal_type = header_byte & 0x1F
    if nal_type != 7:
        return None
    profile_idc = sps_nalu[header_offset + 1]
    constraint_flags = sps_nalu[header_offset + 2]
    level_idc = sps_nalu[header_offset + 3]
    return f"{profile_idc:02x}{constraint_flags:02x}{level_idc:02x}"


def _h264_codec_for_profile(profile_level_id: str) -> list:
    """Build a single-entry H264 codec capability list for
    setCodecPreferences(), with profile-level-id set to the given value.

    Prior fix rounds hardcoded a single profile-level-id constant
    ("42e01f"), reasoning it from a *different* WebRTC session (the
    existing mediamtx/WHEP pipeline) that happened to negotiate that value.
    Real E2E testing against THIS engine, with live scrcpy debug logging,
    found the actual SPS NAL unit currently emitted by the device decodes
    to profile-level-id "42c029" -- genuinely different (different
    constraint flags AND a different level: 4.1 vs 3.1), not a formatting
    quirk. scrcpy/MediaCodec is not configured to force a specific H264
    profile (see scrcpy_session.py) -- the encoder picks its own default,
    which can plausibly vary run to run / device to device. Hardcoding any
    single value is fragile; the only robust fix is to derive
    profile-level-id from the live SPS at connection time (see
    extract_profile_level_id() and run_engine()) and plug it in here.

    IMPORTANT aiortc-specific wrinkle, verified by reading the installed
    source directly (not guessed): RTCRtpTransceiver.setCodecPreferences()
    requires every codec passed in to satisfy `codec in
    get_capabilities(kind).codecs` (aiortc/rtcrtptransceiver.py) -- i.e.
    dataclass *equality* against that list, not merely a compatible shape.
    A freshly hand-built RTCRtpCodecCapability (even one copied from a
    template via dataclasses.replace()) is never `in` that list, since
    equality compares all fields including `parameters`, and raises
    "ValueError: Codec is not in capabilities" (confirmed empirically).
    Separately, createOffer() itself does not use getCapabilities()/
    setCodecPreferences()'s output directly either -- it filters aiortc's
    module-level `aiortc.codecs.CODECS["video"]` list (via
    filter_preferred_codecs() in rtcpeerconnection.py, matched the same way:
    mimeType + parameters equality) to build the actual SDP payload-type
    entries. Both of those checks read from the SAME shared, mutable
    `CODECS["video"]` list, populated once at import time by
    aiortc.codecs.init_codecs() with two fixed H264 profile-level-id
    entries (42001f, 42e01f) baked in.

    The only way to make BOTH setCodecPreferences()'s equality check and
    createOffer()'s SDP generation agree on a profile-level-id that isn't
    one of those two fixed values is to mutate that shared source of truth
    in place, then re-derive fresh capability objects from it (which will
    then correctly satisfy the equality check because both sides are now
    reading the same updated parameters). This mutates the "42e01f" H264
    entry specifically (chosen since it's the one previously proven to
    work with a real hardware decoder, i.e. any future SPS content this
    device emits still lands on the "known-good" table slot) -- this
    process runs exactly one engine/one scrcpy device connection at a
    time, so there is no cross-connection interference from this
    process-wide mutation.
    """
    import aiortc.codecs as _aiortc_codecs

    h264_entries = [
        params
        for params in _aiortc_codecs.CODECS["video"]
        if params.mimeType == "video/H264"
    ]
    # Always target the SAME table slot (the second H264 entry, historically
    # "42e01f") by fixed position, not by matching its current
    # profile-level-id value. Selecting "whichever entry currently equals
    # 42e01f" would break on a second call within the same process (e.g. a
    # reconnect after the device's encoder profile changes): the first call
    # already overwrote that entry's value, so nothing would match "42e01f"
    # on the second call, causing the wrong (first) entry to be mutated
    # instead and leaving both entries with duplicate profile-level-id
    # values -- confirmed via a real repeated-call regression before this
    # fix (two calls with different profile_level_id produced 2 duplicate
    # entries instead of 1 each). Targeting by fixed position is idempotent
    # across any number of calls.
    target = h264_entries[-1]
    target.parameters["profile-level-id"] = profile_level_id

    # Re-derive fresh RTCRtpCodecCapability objects from the now-mutated
    # CODECS table (get_capabilities() always rebuilds from CODECS, never
    # caches) and pick out the one at the same table slot we just mutated,
    # by identity of position rather than re-matching on profile-level-id
    # value -- avoids ambiguity if profile_level_id ever happened to equal
    # the OTHER (untouched) H264 entry's value ("42001f"), which would
    # otherwise make a value-based filter return both.
    caps = RTCRtpSender.getCapabilities("video")
    h264_caps = [codec for codec in caps.codecs if codec.mimeType == "video/H264"]
    return [h264_caps[-1]]


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

    # Read frames from the video socket until we get the first SPS NALU
    # (nal_type==7), which per H264 stream structure is always frame #0 --
    # confirmed via live scrcpy debug logging in a prior fix round. We need
    # its profile-level-id BEFORE building the offer, since that value must
    # be embedded in the SDP's fmtp line and can't be changed after
    # negotiation. A fixed constant was tried in earlier fix rounds and
    # proved fragile: MediaCodec picks its own encoder profile default (see
    # scrcpy_session.py), and a real E2E run showed it emitting a different
    # profile-level-id than what an unrelated WebRTC session (mediamtx/WHEP)
    # had previously negotiated. Deriving it live from the actual SPS is the
    # only robust fix.
    #
    # The frame we consume here to inspect it is not lost -- video_pump_loop()
    # below pushes it to the track first, before resuming iteration of
    # video.read_frames() (an async generator, so it correctly picks up from
    # the SECOND frame onward once video_pump_loop's `async for` starts).
    frames = video.read_frames()
    first_frame = await frames.__anext__()
    live_profile_level_id = extract_profile_level_id(first_frame)
    # EXPERIMENT: declare a browser-guaranteed-negotiable profile-level-id in
    # the SDP (one of the 5 exact values Chrome/Brave's H264 decoder
    # capability list advertises, confirmed via RTCRtpReceiver.getCapabilities
    # in-browser) regardless of what the live SPS actually says. H264 decoders
    # are supposed to configure themselves from the real in-band SPS/PPS in
    # the bitstream, not the SDP string -- profile-level-id in SDP is a
    # negotiation/capability-advertisement hint, not a hard bitstream
    # contract. Testing whether VideoToolbox actually honors that, since
    # dynamically declaring the TRUE live value (e.g. 42c01f, which isn't in
    # the browser's fixed 5-entry table) causes the browser to silently
    # substitute 42e01f during answer generation anyway -- so declaring it
    # ourselves should be equivalent or better, as long as decode genuinely
    # keys off the real bitstream.
    print(f"[debug] first_frame size={len(first_frame)} "
          f"first8={first_frame[:8].hex()} "
          f"live_profile_level_id={live_profile_level_id} "
          f"(declaring 42e01f in SDP regardless, per experiment)", flush=True)
    profile_level_id = "42e01f"

    config = RTCConfiguration(iceServers=[_parse_ice_url(ice_url)])
    pc = RTCPeerConnection(configuration=config)

    track = PassthroughH264Track()
    # Use addTransceiver() instead of addTrack() so we get the
    # RTCRtpTransceiver directly (addTrack() returns only the RTCRtpSender).
    # Constrain the offer to H264-only, with profile-level-id set to the
    # value just derived from the live SPS: aiortc's createOffer() otherwise
    # offers every codec it supports (VP8 included) and lets the remote
    # peer choose -- real E2E testing against a browser showed it picking
    # VP8, which PassthroughH264Track's raw H264 Annex-B NALUs cannot
    # satisfy (the browser decodes H264 bytes as VP8 and nothing renders).
    transceiver = pc.addTransceiver(track, direction="sendrecv")
    transceiver.setCodecPreferences(_h264_codec_for_profile(profile_level_id))

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
    print(f"[debug] offer video profile-level-id(s) in SDP: "
          f"{[line for line in offer.sdp.splitlines() if 'profile-level-id' in line]}",
          flush=True)
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
        # Push the SPS NALU already consumed above (to derive
        # profile_level_id) first -- it's still needed by the decoder --
        # then resume iterating the SAME async generator, which correctly
        # continues from the second frame onward.
        track.push_nalu(first_frame)
        seen_pps = False
        scan_count = 1  # first_frame already counted
        async for nalu in frames:
            if scan_count < 30:
                nt = nalu[4] & 0x1F if len(nalu) > 4 else -1
                if nt == 8 and not seen_pps:
                    seen_pps = True
                    print(f"[debug] PPS (nal_type=8) found at frame #{scan_count}, "
                          f"size={len(nalu)} bytes={nalu[:16].hex()}", flush=True)
                scan_count += 1
                if scan_count == 30 and not seen_pps:
                    print("[debug] NO PPS (nal_type=8) seen in first 30 frames", flush=True)
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
