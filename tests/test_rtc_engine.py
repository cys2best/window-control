import asyncio
import socket
import struct
import pytest

from server.rtc_engine import ScrcpyVideoClient


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
    """Test that stop() alone can clean up blocked read_frames() resources.

    This verifies that stop() correctly sets _running=False and closes sockets
    (via shutdown + close), allowing read_frames() to exit even if it's blocked
    inside sock_recv(). The generator should end cleanly without external
    task cancellation.
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

    # Call stop() to set _running=False and close sockets via shutdown().
    # This should unblock the blocked sock_recv and allow the generator to end.
    client.stop()

    # The consume task should complete cleanly (generator exhausted), not hang or crash.
    await asyncio.wait_for(consume_task, timeout=2.0)


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
