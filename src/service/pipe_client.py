# src/service/pipe_client.py
import json
import sys
import threading
from typing import Callable

from service.pipe_server import CMD_PIPE_NAME, EVENT_PIPE_NAME, encode_msg, decode_msg

if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes

    _kernel32 = ctypes.windll.kernel32

    GENERIC_READ          = 0x80000000
    GENERIC_WRITE         = 0x40000000
    OPEN_EXISTING         = 3
    PIPE_READMODE_MESSAGE = 0x00000002
    INVALID_HANDLE_VALUE  = ctypes.c_void_p(-1).value

    def _open_pipe(name: str):
        # Wait up to 2s for pipe to become available
        _kernel32.WaitNamedPipeW.restype = ctypes.wintypes.BOOL
        _kernel32.WaitNamedPipeW(name, 2000)

        _kernel32.CreateFileW.restype = ctypes.c_void_p
        h = _kernel32.CreateFileW(
            name,
            GENERIC_READ | GENERIC_WRITE,
            0, None,
            OPEN_EXISTING,
            0, None,
        )
        if h == INVALID_HANDLE_VALUE:
            raise OSError(f"CreateFile failed: {ctypes.GetLastError()}")

        # Switch to message-read mode
        mode = ctypes.wintypes.DWORD(PIPE_READMODE_MESSAGE)
        _kernel32.SetNamedPipeHandleState.restype = ctypes.wintypes.BOOL
        _kernel32.SetNamedPipeHandleState(
            ctypes.c_void_p(h),
            ctypes.byref(mode),
            None, None,
        )
        return h

    def _read_pipe(handle, size=65536) -> bytes | None:
        buf = ctypes.create_string_buffer(size)
        read = ctypes.wintypes.DWORD(0)
        _kernel32.ReadFile.restype = ctypes.wintypes.BOOL
        ok = _kernel32.ReadFile(
            ctypes.c_void_p(handle), buf, size,
            ctypes.byref(read), None
        )
        if not ok or read.value == 0:
            return None
        return buf.raw[:read.value]

    def _write_pipe(handle, data: bytes) -> bool:
        written = ctypes.wintypes.DWORD(0)
        _kernel32.WriteFile.restype = ctypes.wintypes.BOOL
        ok = _kernel32.WriteFile(
            ctypes.c_void_p(handle),
            ctypes.c_char_p(data), len(data),
            ctypes.byref(written), None
        )
        return bool(ok)

    def _close_handle(handle):
        try:
            _kernel32.CloseHandle(ctypes.c_void_p(handle))
        except Exception:
            pass

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
            except OSError:
                return False

        def send(self, cmd: dict) -> dict | None:
            if not self._connected or self._cmd_handle is None:
                return None
            with self._cmd_lock:
                try:
                    if not _write_pipe(self._cmd_handle, encode_msg(cmd)):
                        self._connected = False
                        return None
                    data = _read_pipe(self._cmd_handle)
                    if data is None:
                        self._connected = False
                        return None
                    return decode_msg(data)
                except Exception:
                    self._connected = False
                    return None

        def _event_loop(self):
            try:
                event_handle = _open_pipe(EVENT_PIPE_NAME)
            except OSError:
                self._connected = False
                return
            while self._connected:
                data = _read_pipe(event_handle)
                if data is None:
                    break
                msg = decode_msg(data)
                if msg and self._on_event:
                    self._on_event(msg)
            _close_handle(event_handle)
            self._connected = False

        def disconnect(self):
            self._connected = False
            if self._cmd_handle is not None:
                _close_handle(self._cmd_handle)
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
