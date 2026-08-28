import asyncio
import json
import socket
import struct
import pytest

import websockets

from aiortc import RTCPeerConnection

from server.rtc_engine import (
    ScrcpyVideoClient,
    PassthroughH264Track,
    SignalingClient,
    _parse_ice_url,
    _h264_codec_for_profile,
    extract_profile_level_id,
)


class FakeScrcpyServer:
    """Mimics scrcpy-server's video+control accept order and handshake."""

    def __init__(self):
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self._listener.listen(2)
        self.port = self._listener.getsockname()[1]
        self._video_sock = None
        self._control_sock = None

    def accept_video_and_send_dummy(self):
        self._video_sock, _ = self._listener.accept()
        self._video_sock.sendall(b"\x00")

    def accept_control(self):
        self._control_sock, _ = self._listener.accept()

    def send_handshake(self, device_name: str, width: int, height: int):
        name_bytes = device_name.encode("utf-8").ljust(64, b"\x00")
        meta = struct.pack(">III", 0, width, height)  # codec_id unused, width, height
        self._video_sock.sendall(name_bytes + meta)

    def send_frame(self, payload: bytes, pts_flags: int = 0):
        header = struct.pack(">QI", pts_flags, len(payload))
        self._video_sock.sendall(header + payload)

    def close(self):
        if self._video_sock:
            self._video_sock.close()
        if self._control_sock:
            self._control_sock.close()
        self._listener.close()


@pytest.fixture
def fake_server():
    server = FakeScrcpyServer()
    yield server
    server.close()


async def test_connect_reads_dummy_byte(fake_server):
    client = ScrcpyVideoClient(fake_server.port)
    connect_task = asyncio.ensure_future(client.connect())
    await asyncio.get_event_loop().run_in_executor(None, fake_server.accept_video_and_send_dummy)
    await connect_task  # must not raise or hang
    client.stop()


async def test_full_handshake_and_one_frame(fake_server):
    client = ScrcpyVideoClient(fake_server.port)

    async def drive_server():
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, fake_server.accept_video_and_send_dummy)
        await loop.run_in_executor(None, fake_server.accept_control)
        await loop.run_in_executor(
            None, fake_server.send_handshake, "TestDevice", 720, 480
        )
        await loop.run_in_executor(
            None, fake_server.send_frame, b"\x00\x00\x00\x01\x67NALUDATA"
        )

    driver = asyncio.ensure_future(drive_server())
    await client.connect()
    await client.connect_control()
    device_name, width, height = await client.read_handshake()
    await driver

    assert device_name == "TestDevice"
    assert width == 720
    assert height == 480

    frames = client.read_frames()
    first_frame = await frames.__anext__()
    assert first_frame == b"\x00\x00\x00\x01\x67NALUDATA"

    client.stop()


async def test_stop_interrupts_blocked_read_frames(fake_server):
    """stop() ALONE must promptly unblock a read_frames() consumer.

    Regression test for the real bug: stop() used to rely on
    socket.shutdown()+close() to wake a blocked loop.sock_recv(). That does
    NOT reliably interrupt a selector-registered sock_recv() — the awaiting
    Future can sit forever with no error and no completion. The fix is
    task cancellation: stop() must cancel the exact asyncio Task blocked
    inside read_frames().

    Uses a SHORT wait_for timeout (0.5s, not a generous 2s+) deliberately:
    a regression back to the broken socket-shutdown approach must fail fast
    and obviously by hitting this short timeout, not silently "pass" by
    happening to complete just under a generous one. This is exactly the
    false-positive pattern that hid the bug for three prior fix rounds.
    """
    client = ScrcpyVideoClient(fake_server.port)

    async def drive_server():
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, fake_server.accept_video_and_send_dummy)
        await loop.run_in_executor(None, fake_server.accept_control)
        await loop.run_in_executor(
            None, fake_server.send_handshake, "TestDevice", 720, 480
        )
        # Deliberately send no more frames — leaves read_frames() blocked on the
        # next 12-byte header read inside _recvall().

    driver = asyncio.ensure_future(drive_server())
    await client.connect()
    await client.connect_control()
    await client.read_handshake()
    await driver

    async def consume():
        """Consume frames from read_frames() until it ends."""
        async for _ in client.read_frames():
            pass

    consume_task = asyncio.ensure_future(consume())
    await asyncio.sleep(0.1)  # let it actually block inside sock_recv

    # Call stop() alone -- no external task.cancel() from the test itself.
    # stop() must internally cancel the task blocked in read_frames().
    start = asyncio.get_event_loop().time()
    client.stop()

    # Short timeout: proves the unblock is immediate (task cancellation),
    # not "eventually" (which would indicate the old broken mechanism, or
    # no mechanism at all, papered over by a generous timeout).
    await asyncio.wait_for(consume_task, timeout=0.5)
    elapsed = asyncio.get_event_loop().time() - start
    assert elapsed < 0.5, f"stop() took {elapsed:.3f}s to unblock read_frames(); expected near-instant"

    # The task ended cleanly (generator exhausted), NOT via CancelledError
    # propagating out -- stop()-triggered cancellation is swallowed internally.
    assert not consume_task.cancelled()
    assert consume_task.exception() is None


async def test_cancel_propagates_through_read_frames(fake_server):
    """Test that asyncio.CancelledError propagates correctly through read_frames().

    This verifies that external cancellation (via task.cancel()) properly
    propagates out of read_frames() as CancelledError, and the task transitions
    to a cancelled state (task.cancelled() == True). This ensures proper
    semantics for asyncio.wait_for() timeouts and graceful shutdown.
    """
    client = ScrcpyVideoClient(fake_server.port)

    async def drive_server():
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, fake_server.accept_video_and_send_dummy)
        await loop.run_in_executor(None, fake_server.accept_control)
        await loop.run_in_executor(
            None, fake_server.send_handshake, "TestDevice", 720, 480
        )
        # Deliberately send no more frames — leaves read_frames() blocked,
        # waiting to be cancelled.

    driver = asyncio.ensure_future(drive_server())
    await client.connect()
    await client.connect_control()
    await client.read_handshake()
    await driver

    async def consume():
        """Consume frames from read_frames() until it ends."""
        async for _ in client.read_frames():
            pass  # should never yield; loop ends when cancelled

    consume_task = asyncio.ensure_future(consume())
    await asyncio.sleep(0.1)  # let it actually block inside sock_recv

    # Cancel the task directly; CancelledError should propagate.
    consume_task.cancel()

    # CancelledError must be raised, not swallowed.
    with pytest.raises(asyncio.CancelledError):
        await consume_task

    # After awaiting a cancelled task, task.cancelled() must return True.
    assert consume_task.cancelled()
    client.stop()


async def test_recv_wraps_nalu_in_av_packet():
    import av

    track = PassthroughH264Track()
    track.push_nalu(b"\x00\x00\x00\x01\x67SPSDATA")

    packet = await track.recv()

    assert isinstance(packet, av.Packet)
    assert bytes(packet) == b"\x00\x00\x00\x01\x67SPSDATA"


async def test_recv_yields_multiple_nalus_in_order():
    track = PassthroughH264Track()
    track.push_nalu(b"\x00\x00\x00\x01\x67SPS")
    track.push_nalu(b"\x00\x00\x00\x01\x68PPS")

    first = await track.recv()
    second = await track.recv()

    assert bytes(first) == b"\x00\x00\x00\x01\x67SPS"
    assert bytes(second) == b"\x00\x00\x00\x01\x68PPS"


async def test_stop_then_external_cancel_no_deadlock(fake_server):
    """Realistic shutdown race: engine calls stop() first, then whatever is
    consuming read_frames() is also torn down (its wrapping task cancelled).

    This is the round-3-flagged danger case: stop() cancels self._read_task
    internally (swallowing that CancelledError so the generator ends
    cleanly), and then the caller's own task.cancel() arrives on top of
    that. Must not deadlock, must not silently swallow a genuine external
    cancellation, and must behave reasonably either way.

    Ordering chosen to match how Task 1's consumers in this plan will use
    the class: stop() is called first during engine shutdown, then the
    task consuming frames naturally winds down/gets cancelled.
    """
    client = ScrcpyVideoClient(fake_server.port)

    async def drive_server():
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, fake_server.accept_video_and_send_dummy)
        await loop.run_in_executor(None, fake_server.accept_control)
        await loop.run_in_executor(
            None, fake_server.send_handshake, "TestDevice", 720, 480
        )
        # No more frames -- read_frames() blocks waiting for the next header.

    driver = asyncio.ensure_future(drive_server())
    await client.connect()
    await client.connect_control()
    await client.read_handshake()
    await driver

    async def consume():
        async for _ in client.read_frames():
            pass

    consume_task = asyncio.ensure_future(consume())
    await asyncio.sleep(0.1)  # let it actually block inside sock_recv

    # Realistic shutdown sequence: stop() first (engine shutdown), then the
    # consumer's own wrapping task is cancelled in close succession.
    client.stop()
    consume_task.cancel()

    # No deadlock: this must resolve promptly regardless of which
    # cancellation "wins" the race.
    try:
        await asyncio.wait_for(asyncio.shield(consume_task), timeout=0.5)
        # stop()'s internal cancel landed first and was swallowed cleanly;
        # the task finished normally before the external cancel could take
        # effect on it.
        assert consume_task.exception() is None
    except asyncio.CancelledError:
        # The external consume_task.cancel() won the race instead -- also a
        # legitimate, correctly-propagated outcome (real cancellation, not a
        # hang and not a silently swallowed error).
        assert consume_task.cancelled()


@pytest.fixture
async def echo_ws_server():
    received = []

    async def handler(ws):
        async for message in ws:
            received.append(message)
            await ws.send(message)  # echo back

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    yield f"ws://127.0.0.1:{port}", received
    server.close()
    await server.wait_closed()


async def test_connect_sends_session_and_role_in_url():
    captured = {}

    async def handler(ws):
        captured["path"] = ws.request.path
        async for _ in ws:
            pass

    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        client = SignalingClient(f"ws://127.0.0.1:{port}", "sess-42", "engine", "tok123")
        await client.connect()
        await client.close()
        await asyncio.sleep(0.05)  # let the server-side handler observe the path
    finally:
        server.close()
        await server.wait_closed()

    assert "session=sess-42" in captured["path"]
    assert "role=engine" in captured["path"]
    assert "token=tok123" in captured["path"]


async def test_send_and_recv_roundtrip(echo_ws_server):
    base_url, received = echo_ws_server
    client = SignalingClient(base_url, "sess-1", "engine", "")
    await client.connect()

    await client.send({"type": "offer", "sdp": "test-sdp"})
    reply = await client.recv()

    assert reply == {"type": "offer", "sdp": "test-sdp"}
    assert json.loads(received[0]) == {"type": "offer", "sdp": "test-sdp"}
    await client.close()


async def test_recv_returns_none_for_malformed_json(echo_ws_server):
    """recv() must return None for syntactically invalid JSON, not raise."""
    base_url, received = echo_ws_server
    client = SignalingClient(base_url, "sess-1", "engine", "")
    await client.connect()

    # Send genuinely malformed JSON (not valid JSON at all)
    malformed = "not valid json{{{"
    await client._ws.send(malformed)
    result = await client.recv()

    assert result is None
    await client.close()


async def test_recv_returns_none_for_non_dict_valid_json(echo_ws_server):
    """recv() must return None for valid-but-non-dict JSON (strings, numbers, lists, null)."""
    base_url, received = echo_ws_server
    client = SignalingClient(base_url, "sess-1", "engine", "")
    await client.connect()

    # Test valid string JSON
    await client._ws.send('"hello"')
    result = await client.recv()
    assert result is None

    # Test valid number JSON
    await client._ws.send("42")
    result = await client.recv()
    assert result is None

    # Test valid list JSON
    await client._ws.send("[1, 2, 3]")
    result = await client.recv()
    assert result is None

    # Test null JSON
    await client._ws.send("null")
    result = await client.recv()
    assert result is None

    # Verify that a valid dict still works
    await client._ws.send('{"type": "test"}')
    result = await client.recv()
    assert result == {"type": "test"}

    await client.close()


from unittest.mock import MagicMock

from server.rtc_engine import handle_input_message
from server.scrcpy_session import ScrcpyControl


def test_handle_tap_down_calls_send_touch():
    control = MagicMock(spec=ScrcpyControl)
    control.ACTION_DOWN = ScrcpyControl.ACTION_DOWN

    handle_input_message(
        control,
        '{"type": "tap", "action": "down", "nx": 0.5, "ny": 0.25}',
        screen_width=720,
        screen_height=480,
    )

    control.send_touch.assert_called_once_with(
        ScrcpyControl.ACTION_DOWN, 0.5, 0.25, 720, 480
    )


def test_handle_key_calls_send_keycode():
    control = MagicMock(spec=ScrcpyControl)

    handle_input_message(
        control, '{"type": "key", "keycode": 4}', screen_width=720, screen_height=480
    )

    control.send_keycode.assert_called_once_with(4)


def test_handle_malformed_json_is_swallowed():
    control = MagicMock(spec=ScrcpyControl)

    handle_input_message(control, "not json", screen_width=720, screen_height=480)

    control.send_touch.assert_not_called()
    control.send_keycode.assert_not_called()


def test_handle_key_zero_is_ignored():
    control = MagicMock(spec=ScrcpyControl)

    handle_input_message(
        control, '{"type": "key", "keycode": 0}', screen_width=720, screen_height=480
    )

    control.send_keycode.assert_not_called()


def test_parse_ice_url_splits_combined_turn_credentials():
    """The 4th CLI arg arrives as a combined `turn:user:pass@host:port`
    string. aiortc's RTCIceServer.urls must be the bare `turn:host:port`
    part only, with username/credential as separate fields -- otherwise
    aiortc.rtcicetransport.parse_stun_turn_uri() raises
    `ValueError: malformed uri` the moment a track is added.
    """
    server = _parse_ice_url("turn:poc-user:poc-secret-change-me@13.214.163.82:3478")

    assert server.urls == "turn:13.214.163.82:3478"
    assert server.username == "poc-user"
    assert server.credential == "poc-secret-change-me"


def test_parse_ice_url_passes_through_bare_stun_url():
    """A bare STUN URL with no embedded credentials must pass through
    unchanged, with username/credential left at their None defaults."""
    server = _parse_ice_url("stun:stun.l.google.com:19302")

    assert server.urls == "stun:stun.l.google.com:19302"
    assert server.username is None
    assert server.credential is None


def test_handle_non_dict_valid_json_is_swallowed():
    """handle_input_message must not crash on valid-but-non-dict JSON.

    json.loads() succeeds for any legal JSON document, including strings,
    numbers, lists, and null. The function must treat these as malformed
    input (swallow silently, no exception) rather than crashing with AttributeError
    when trying to call .get() on a non-dict.
    """
    control = MagicMock(spec=ScrcpyControl)

    # Test valid string JSON
    handle_input_message(control, '"hello"', screen_width=720, screen_height=480)
    control.send_touch.assert_not_called()
    control.send_keycode.assert_not_called()

    control.reset_mock()

    # Test valid number JSON
    handle_input_message(control, "42", screen_width=720, screen_height=480)
    control.send_touch.assert_not_called()
    control.send_keycode.assert_not_called()

    control.reset_mock()

    # Test valid list JSON
    handle_input_message(control, "[1, 2, 3]", screen_width=720, screen_height=480)
    control.send_touch.assert_not_called()
    control.send_keycode.assert_not_called()

    control.reset_mock()

    # Test null JSON
    handle_input_message(control, "null", screen_width=720, screen_height=480)
    control.send_touch.assert_not_called()
    control.send_keycode.assert_not_called()


def test_h264_codec_for_profile_excludes_vp8_and_rtx():
    """_h264_codec_for_profile() must return only H264 entries, never VP8
    or video/rtx.

    Manual E2E testing against a real browser found that the negotiated
    codec was VP8 (97) via libvpx, not H264 -- because nothing constrained
    createOffer() to H264-only, so aiortc offered every codec it supports
    (VP8 and video/rtx included, confirmed via RTCRtpSender.getCapabilities
    ("video")) and the browser picked VP8. PassthroughH264Track only ever
    produces raw H264 Annex-B NALUs, so a VP8-negotiated connection cannot
    decode anything. This test locks down the filtering helper against the
    real installed aiortc codec capability list, not a hand-rolled fake.
    """
    codecs = _h264_codec_for_profile("42c029")

    assert len(codecs) >= 1
    assert all(codec.mimeType == "video/H264" for codec in codecs)
    assert not any(codec.mimeType == "video/VP8" for codec in codecs)
    assert not any(codec.mimeType == "video/rtx" for codec in codecs)


def test_h264_codec_for_profile_sets_requested_profile_level_id():
    """_h264_codec_for_profile() must embed the EXACT profile-level-id it
    was given, not a hardcoded constant.

    A prior fix round hardcoded profile-level-id=42e01f, reasoned from a
    *different* WebRTC session (the existing mediamtx/WHEP pipeline) that
    happened to negotiate that value. Real E2E testing against this engine,
    with live scrcpy debug logging, found the device's actual live SPS
    decodes to profile-level-id=42c029 -- genuinely different (different
    constraint flags AND level). scrcpy/MediaCodec is not configured to
    force a specific H264 profile (see scrcpy_session.py) -- the encoder
    picks its own default, which can plausibly vary. The only robust fix is
    deriving profile-level-id live from the SPS and threading it through
    here dynamically, so this test locks down that the function's output
    actually reflects its argument rather than any fixed constant.
    """
    codecs = _h264_codec_for_profile("42c029")

    assert len(codecs) == 1
    assert codecs[0].mimeType == "video/H264"
    assert codecs[0].parameters.get("profile-level-id") == "42c029"

    # Also verify a different input produces a different output -- proves
    # this isn't silently ignoring its argument and falling back to some
    # baked-in default.
    other_codecs = _h264_codec_for_profile("640028")
    assert other_codecs[0].parameters.get("profile-level-id") == "640028"


def test_extract_profile_level_id_from_real_captured_sps():
    """extract_profile_level_id() must correctly parse the real SPS bytes
    captured via live scrcpy debug logging during E2E testing.

    Captured bytes (hex, spaces for readability only, not in the real
    stream): "0000000167 42c0298d680b435f964200". Start code 00 00 00 01,
    NAL header 0x67 (nal_type=7=SPS), then profile_idc=0x42,
    constraint_flags=0xc0, level_idc=0x29 -- decoding to "42c029". This is
    NOT the same as the previously-hardcoded "42e01f": profile_idc matches
    (0x42=Baseline in both) but constraint_flags (0xc0 vs 0xe0) and
    level_idc (0x29=level 4.1 vs 0x1f=level 3.1) genuinely differ.
    """
    sps_nalu = bytes.fromhex("0000000167 42c0298d680b435f964200".replace(" ", ""))

    assert extract_profile_level_id(sps_nalu) == "42c029"


def test_extract_profile_level_id_accepts_3_byte_start_code():
    """Annex-B allows either a 3-byte (00 00 01) or 4-byte (00 00 00 01)
    start code; the extractor must handle both since real encoder output
    is not guaranteed to always emit the 4-byte form."""
    sps_nalu = bytes.fromhex("000001" + "6742c0298d680b435f964200")

    assert extract_profile_level_id(sps_nalu) == "42c029"


def test_extract_profile_level_id_returns_none_for_non_sps_nalu():
    """A non-SPS NALU (e.g. nal_type=1, a regular slice) must return None,
    not misinterpret arbitrary payload bytes as profile-level-id."""
    non_sps = bytes.fromhex("00000001" + "419A24" + "AABBCC")

    assert extract_profile_level_id(non_sps) is None


def test_extract_profile_level_id_returns_none_for_too_short_input():
    """Input too short to contain a start code + NAL header + 3 profile
    bytes must return None rather than raising IndexError."""
    assert extract_profile_level_id(b"\x00\x00\x00\x01\x67\x42") is None
    assert extract_profile_level_id(b"") is None


async def test_offer_sdp_constrained_to_h264_only_with_dynamic_profile():
    """End-to-end proof that the fix actually constrains the generated SDP
    offer to a dynamically-derived profile-level-id, not just that the
    helper functions return the right values in isolation.

    Builds a real RTCPeerConnection + PassthroughH264Track using the same
    addTransceiver()/setCodecPreferences() sequence run_engine() now uses
    (deriving profile-level-id from a simulated live SPS via
    extract_profile_level_id(), then _h264_codec_for_profile()), then
    inspects the real offer SDP: the video m= line's payload types must
    resolve only to H264 rtpmap entries with the derived profile-level-id,
    and VP8's mimeType must not appear anywhere in the SDP at all.
    """
    sps_nalu = bytes.fromhex("0000000167 42c0298d680b435f964200".replace(" ", ""))
    profile_level_id = extract_profile_level_id(sps_nalu)
    assert profile_level_id == "42c029"

    pc = RTCPeerConnection()
    try:
        track = PassthroughH264Track()
        transceiver = pc.addTransceiver(track, direction="sendrecv")
        transceiver.setCodecPreferences(_h264_codec_for_profile(profile_level_id))

        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        sdp = pc.localDescription.sdp

        video_m_line = next(line for line in sdp.splitlines() if line.startswith("m=video"))
        rtpmap_lines = [line for line in sdp.splitlines() if line.startswith("a=rtpmap:")]

        assert rtpmap_lines, "offer SDP has no rtpmap lines for video payload types"
        assert all("H264" in line for line in rtpmap_lines), (
            f"non-H264 codec present in offer rtpmap lines: {rtpmap_lines}"
        )
        assert "VP8" not in sdp, f"VP8 leaked into offer SDP: {video_m_line}"

        # profile-level-id must match exactly what was derived from the live
        # SPS above (42c029) -- not any hardcoded constant from a prior fix
        # round (42e01f) or the other static aiortc capability (42001f).
        fmtp_lines = [line for line in sdp.splitlines() if line.startswith("a=fmtp:")]
        assert fmtp_lines, "offer SDP has no fmtp lines for video payload types"
        assert all("profile-level-id=42c029" in line for line in fmtp_lines), (
            f"derived profile-level-id missing from offer fmtp lines: {fmtp_lines}"
        )
        assert "42e01f" not in sdp, f"stale hardcoded profile leaked into offer SDP: {sdp}"
        assert "42001f" not in sdp, f"42001f profile leaked into offer SDP: {sdp}"
    finally:
        await pc.close()


async def _to_async_iter(items):
    for item in items:
        yield item


async def test_group_into_access_units_combines_params_split_across_chunks_with_slice():
    """Root-cause fix for the black-frame bug: scrcpy sometimes emits SPS/PPS
    in separate wire chunks from the slice that uses them (confirmed via live
    capture during the aiortc migration's Phase 0 investigation -- see
    docs/superpowers/plans/2026-08-28-mediamtx-aiortc-migration.md). Pushed
    as separate av.Packets, aiortc's RTCRtpSender gives each its own RTP
    timestamp and marker bit (rtcrtpsender.py's _next_encoded_frame/_run_rtp
    call track.recv() once per RTP "frame"), breaking RFC 6184 access-unit
    grouping. This groups them back into one access unit per slice.
    """
    from server.rtc_engine import group_into_access_units

    sps = b"\x00\x00\x00\x01\x67sps-bytes"
    pps_with_repeated_sps = b"\x00\x00\x00\x01\x67sps-again\x00\x00\x00\x01\x68pps-bytes"
    idr_slice = b"\x00\x00\x00\x01\x65idr-slice-bytes"

    chunks = _to_async_iter([sps, pps_with_repeated_sps, idr_slice])
    access_units = [au async for au in group_into_access_units(chunks)]

    assert access_units == [sps + pps_with_repeated_sps + idr_slice]


async def test_group_into_access_units_yields_slice_only_chunk_immediately():
    from server.rtc_engine import group_into_access_units

    idr_slice = b"\x00\x00\x00\x01\x65idr-slice-bytes"
    non_idr_slice = b"\x00\x00\x00\x01\x61p-slice-bytes"

    chunks = _to_async_iter([idr_slice, non_idr_slice])
    access_units = [au async for au in group_into_access_units(chunks)]

    assert access_units == [idr_slice, non_idr_slice]


async def test_group_into_access_units_drops_trailing_params_with_no_slice():
    """A parameter-set chunk with no following slice (e.g. stream ends mid-
    access-unit) is buffered and never flushed -- matching the existing
    drop-on-shutdown behavior elsewhere in this codebase (_NaluWriteQueue's
    close() sentinel) rather than emitting an incomplete access unit.
    """
    from server.rtc_engine import group_into_access_units

    sps = b"\x00\x00\x00\x01\x67sps-bytes"
    chunks = _to_async_iter([sps])
    access_units = [au async for au in group_into_access_units(chunks)]

    assert access_units == []
