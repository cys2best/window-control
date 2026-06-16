# src/service/pipe_client.py
import json
import sys
import threading
from typing import Callable

from service.pipe_server import CMD_PIPE_NAME, EVENT_PIPE_NAME, encode_msg, decode_msg

if sys.platform == "win32":
    import win32pipe
    import win32file
    import pywintypes

    def _open_pipe(name: str):
        win32pipe.WaitNamedPipe(name, 2000)
        handle = win32file.CreateFile(
            name,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0, None,
            win32file.OPEN_EXISTING,
            0, None,
        )
        win32pipe.SetNamedPipeHandleState(
            handle,
            win32pipe.PIPE_READMODE_MESSAGE,
            None, None,
        )
        return handle

    class PipeClient:
        """Two-pipe client.

        CMD pipe  — send commands to service, get replies.
        EVENT pipe — receive unsolicited events pushed by service.
        """

        def __init__(self, on_event: Callable[[dict], None] | None = None):
            self._on_event = on_event
            self._cmd_handle = None
            self._cmd_lock = threading.Lock()
            self._connected = False

        def connect(self) -> bool:
            try:
                self._cmd_handle = _open_pipe(CMD_PIPE_NAME)
                self._connected = True
                if self._on_event:
                    t = threading.Thread(target=self._event_loop, daemon=True)
                    t.start()
                return True
            except pywintypes.error:
                return False

        def send(self, cmd: dict) -> dict | None:
            """Send command, wait for reply."""
            if not self._connected or self._cmd_handle is None:
                return None
            with self._cmd_lock:
                try:
                    win32file.WriteFile(self._cmd_handle, encode_msg(cmd))
                    _, data = win32file.ReadFile(self._cmd_handle, 65536)
                    return decode_msg(data)
                except pywintypes.error:
                    self._connected = False
                    return None

        def _event_loop(self):
            """Separate connection to EVENT pipe — reads unsolicited pushes."""
            try:
                event_handle = _open_pipe(EVENT_PIPE_NAME)
            except pywintypes.error:
                self._connected = False
                return
            while self._connected:
                try:
                    _, data = win32file.ReadFile(event_handle, 65536)
                    msg = decode_msg(data)
                    if msg and self._on_event:
                        self._on_event(msg)
                except pywintypes.error:
                    break
            try:
                event_handle.Close()
            except Exception:
                pass
            # Mark disconnected so _try_connect_pipe retries
            self._connected = False

        def disconnect(self):
            self._connected = False
            if self._cmd_handle:
                try:
                    self._cmd_handle.Close()
                except Exception:
                    pass
                self._cmd_handle = None

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
