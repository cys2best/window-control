# src/service/pipe_server.py
import json
import sys
import threading
from typing import Callable

PIPE_NAME = r"\\.\pipe\WindowControlPipe"


def encode_msg(obj: dict) -> bytes:
    return json.dumps(obj).encode("utf-8") + b"\n"


def decode_msg(data: bytes) -> dict | None:
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except Exception:
        return None


if sys.platform == "win32":
    import win32pipe
    import win32file
    import win32security
    import pywintypes

    def _create_pipe_handle():
        sd = win32security.SECURITY_DESCRIPTOR()
        sd.SetSecurityDescriptorDacl(True, None, False)  # allow all
        sa = win32security.SECURITY_ATTRIBUTES()
        sa.SECURITY_DESCRIPTOR = sd
        return win32pipe.CreateNamedPipe(
            PIPE_NAME,
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
            win32pipe.PIPE_UNLIMITED_INSTANCES,
            65536, 65536,
            0,
            sa,
        )

    class PipeServer:
        """Runs in service. Accepts one client at a time in a loop."""

        def __init__(self, on_command: Callable[[dict], dict | None]):
            self._on_command = on_command
            self._running = False
            self._thread: threading.Thread | None = None

        def start(self):
            self._running = True
            self._thread = threading.Thread(target=self._serve_loop, daemon=True)
            self._thread.start()

        def stop(self):
            self._running = False

        def _serve_loop(self):
            while self._running:
                try:
                    handle = _create_pipe_handle()
                    win32pipe.ConnectNamedPipe(handle, None)
                    self._handle_client(handle)
                except Exception:
                    pass

        def _handle_client(self, handle):
            try:
                while self._running:
                    try:
                        _, data = win32file.ReadFile(handle, 65536)
                    except pywintypes.error:
                        break
                    msg = decode_msg(data.rstrip(b"\n"))
                    if msg is None:
                        continue
                    reply = self._on_command(msg)
                    if reply is not None:
                        win32file.WriteFile(handle, encode_msg(reply))
            finally:
                try:
                    handle.Close()
                except Exception:
                    pass

        def push(self, handle, event: dict):
            """Push unsolicited event to connected client."""
            try:
                win32file.WriteFile(handle, encode_msg(event))
            except Exception:
                pass

else:
    class PipeServer:
        def __init__(self, on_command):
            pass
        def start(self):
            pass
        def stop(self):
            pass
