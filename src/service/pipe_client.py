# src/service/pipe_client.py
import json
import sys
import threading
from typing import Callable

from service.pipe_server import PIPE_NAME, encode_msg, decode_msg

if sys.platform == "win32":
    import win32pipe
    import win32file
    import pywintypes

    class PipeClient:
        """Runs in GUI tray app. Connects to service pipe, sends commands, reads events."""

        def __init__(self, on_event: Callable[[dict], None] | None = None):
            self._on_event = on_event
            self._handle = None
            self._lock = threading.Lock()
            self._connected = False

        def connect(self) -> bool:
            try:
                win32pipe.WaitNamedPipe(PIPE_NAME, 2000)
                self._handle = win32file.CreateFile(
                    PIPE_NAME,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0, None,
                    win32file.OPEN_EXISTING,
                    0, None,
                )
                win32pipe.SetNamedPipeHandleState(
                    self._handle,
                    win32pipe.PIPE_READMODE_MESSAGE,
                    None, None,
                )
                self._connected = True
                if self._on_event:
                    t = threading.Thread(target=self._read_loop, daemon=True)
                    t.start()
                return True
            except pywintypes.error:
                return False

        def send(self, cmd: dict) -> dict | None:
            if not self._connected or self._handle is None:
                return None
            with self._lock:
                try:
                    win32file.WriteFile(self._handle, encode_msg(cmd))
                    _, data = win32file.ReadFile(self._handle, 65536)
                    return decode_msg(data.rstrip(b"\n"))
                except pywintypes.error:
                    self._connected = False
                    return None

        def _read_loop(self):
            while self._connected and self._handle is not None:
                try:
                    _, data = win32file.ReadFile(self._handle, 65536)
                    msg = decode_msg(data.rstrip(b"\n"))
                    if msg and self._on_event:
                        self._on_event(msg)
                except pywintypes.error:
                    self._connected = False
                    break

        def disconnect(self):
            self._connected = False
            if self._handle:
                try:
                    self._handle.Close()
                except Exception:
                    pass
                self._handle = None

        @property
        def is_connected(self) -> bool:
            return self._connected

else:
    class PipeClient:
        def __init__(self, on_event=None):
            self._connected = False
        def connect(self) -> bool:
            return False
        def send(self, cmd: dict) -> dict | None:
            return None
        def disconnect(self):
            pass
        @property
        def is_connected(self) -> bool:
            return False
