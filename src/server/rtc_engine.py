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
import socket
import struct
from typing import AsyncIterator


class ScrcpyVideoClient:
    def __init__(self, port: int):
        self._port = port
        self._sock: socket.socket | None = None
        self.control_sock: socket.socket | None = None
        self._running = False

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
            try:
                chunk = await loop.sock_recv(self._sock, n - len(buf))
            except (OSError, ValueError):
                # Socket closed, unregistered, or stop() called; treat as EOF
                raise ConnectionError("ScrcpyVideoClient: connection closed mid-read")
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
        self._running = True
        while self._running:
            try:
                header = await self._recvall(12)
            except ConnectionError:
                break
            _pts_flags, size = struct.unpack(">QI", header)
            if size == 0:
                payload = b""
            else:
                payload = await self._recvall(size)
            yield payload

    def stop(self) -> None:
        self._running = False
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
