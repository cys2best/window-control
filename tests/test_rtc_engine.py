import asyncio
import json
import socket
import struct
import pytest

import websockets

from server.rtc_engine import ScrcpyVideoClient, PassthroughH264Track, SignalingClient


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
