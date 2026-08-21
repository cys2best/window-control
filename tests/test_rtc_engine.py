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
