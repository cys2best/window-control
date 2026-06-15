# src/service/pipe_server.py
import json
import sys
import threading
from typing import Callable

CMD_PIPE_NAME   = r"\\.\pipe\WindowControlCmd"
EVENT_PIPE_NAME = r"\\.\pipe\WindowControlEvents"

# Keep old name for any code that still imports it
PIPE_NAME = CMD_PIPE_NAME


def encode_msg(obj: dict) -> bytes:
    return json.dumps(obj).encode("utf-8") + b"\n"


def decode_msg(data: bytes) -> dict | None:
    if not data:
        return None
    try:
        return json.loads(data.decode("utf-8").strip())
    except Exception:
        return None


if sys.platform == "win32":
    import win32pipe
    import win32file
    import win32security
    import pywintypes

    def _create_pipe_handle(name: str):
        sd = win32security.SECURITY_DESCRIPTOR()
        sd.SetSecurityDescriptorDacl(True, None, False)  # allow all
        sa = win32security.SECURITY_ATTRIBUTES()
        sa.SECURITY_DESCRIPTOR = sd
        return win32pipe.CreateNamedPipe(
            name,
            win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_MESSAGE | win32pipe.PIPE_READMODE_MESSAGE | win32pipe.PIPE_WAIT,
            win32pipe.PIPE_UNLIMITED_INSTANCES,
            65536, 65536,
            0,
            sa,
        )

    class PipeServer:
        """Two-pipe server.

        CMD pipe  — GUI sends commands, service replies (request/reply).
        EVENT pipe — service pushes unsolicited events to GUI (write-only from service side).
        """

        def __init__(self, on_command: Callable[[dict], dict | None],
                     on_connect: Callable[[], None] | None = None,
                     on_disconnect: Callable[[], None] | None = None):
            self._on_command = on_command
            self._on_connect = on_connect
            self._on_disconnect = on_disconnect
            self._running = False
            self._cmd_thread: threading.Thread | None = None
            self._event_thread: threading.Thread | None = None
            self._event_handle = None
            self._event_lock = threading.Lock()

        def start(self):
            self._running = True
            self._cmd_thread = threading.Thread(target=self._cmd_loop, daemon=True)
            self._cmd_thread.start()
            self._event_thread = threading.Thread(target=self._event_loop, daemon=True)
            self._event_thread.start()

        def stop(self):
            self._running = False

        def _cmd_loop(self):
            """Accept CMD pipe clients — one at a time, request/reply."""
            import time
            while self._running:
                try:
                    handle = _create_pipe_handle(CMD_PIPE_NAME)
                    win32pipe.ConnectNamedPipe(handle, None)
                    if self._on_connect:
                        self._on_connect()
                    self._handle_cmd_client(handle)
                    if self._on_disconnect:
                        self._on_disconnect()
                except Exception:
                    time.sleep(0.1)

        def _handle_cmd_client(self, handle):
            try:
                while self._running:
                    try:
                        _, data = win32file.ReadFile(handle, 65536)
                    except pywintypes.error:
                        break
                    msg = decode_msg(data)
                    if msg is None:
                        continue
                    reply = self._on_command(msg)
                    if reply is not None:
                        try:
                            win32file.WriteFile(handle, encode_msg(reply))
                        except pywintypes.error:
                            break
            finally:
                try:
                    handle.Close()
                except Exception:
                    pass

        def _event_loop(self):
            """Accept EVENT pipe client — hold connection, push events."""
            import time
            while self._running:
                try:
                    handle = _create_pipe_handle(EVENT_PIPE_NAME)
                    win32pipe.ConnectNamedPipe(handle, None)
                    with self._event_lock:
                        self._event_handle = handle
                    # Wait until client disconnects (read will error on disconnect)
                    while self._running:
                        try:
                            # Peek — non-blocking check if client disconnected
                            win32pipe.PeekNamedPipe(handle, 0)
                        except pywintypes.error:
                            break
                        import time as _t; _t.sleep(0.5)
                    with self._event_lock:
                        self._event_handle = None
                    try:
                        handle.Close()
                    except Exception:
                        pass
                except Exception:
                    time.sleep(0.1)

        def push(self, event: dict):
            """Push unsolicited event to connected GUI client via EVENT pipe."""
            with self._event_lock:
                handle = self._event_handle
            if handle is None:
                return
            try:
                win32file.WriteFile(handle, encode_msg(event))
            except Exception:
                pass

else:
    class PipeServer:
        def __init__(self, on_command, on_connect=None, on_disconnect=None):
            pass
        def start(self):
            pass
        def stop(self):
            pass
        def push(self, event: dict):
            pass
