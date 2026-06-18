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
    import ctypes
    import ctypes.wintypes

    _kernel32 = ctypes.windll.kernel32

    PIPE_ACCESS_DUPLEX       = 0x00000003
    PIPE_TYPE_MESSAGE        = 0x00000004
    PIPE_READMODE_MESSAGE    = 0x00000002
    PIPE_WAIT                = 0x00000000
    PIPE_UNLIMITED_INSTANCES = 255
    INVALID_HANDLE_VALUE     = ctypes.c_void_p(-1).value
    GENERIC_READ             = 0x80000000
    GENERIC_WRITE            = 0x40000000
    OPEN_EXISTING            = 3
    ERROR_PIPE_CONNECTED     = 535
    ERROR_BROKEN_PIPE        = 109
    ERROR_NO_DATA            = 232

    def _create_pipe_handle(name: str):
        # NULL DACL security descriptor — allow all
        _advapi32 = ctypes.windll.advapi32
        sd = ctypes.create_string_buffer(ctypes.sizeof(ctypes.c_uint) * 5 + 8)
        _advapi32.InitializeSecurityDescriptor(sd, 1)
        _advapi32.SetSecurityDescriptorDacl(sd, True, None, False)

        class SECURITY_ATTRIBUTES(ctypes.Structure):
            _fields_ = [
                ("nLength",              ctypes.wintypes.DWORD),
                ("lpSecurityDescriptor", ctypes.c_void_p),
                ("bInheritHandle",       ctypes.wintypes.BOOL),
            ]
        sa = SECURITY_ATTRIBUTES()
        sa.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
        sa.lpSecurityDescriptor = ctypes.cast(sd, ctypes.c_void_p)
        sa.bInheritHandle = False

        _kernel32.CreateNamedPipeW.restype = ctypes.c_void_p
        h = _kernel32.CreateNamedPipeW(
            name,
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT,
            PIPE_UNLIMITED_INSTANCES,
            65536, 65536, 0,
            ctypes.byref(sa),
        )
        if h == INVALID_HANDLE_VALUE:
            raise OSError(f"CreateNamedPipe failed: {ctypes.GetLastError()}")
        return h

    def _connect_pipe(handle) -> bool:
        _kernel32.ConnectNamedPipe.restype = ctypes.wintypes.BOOL
        ret = _kernel32.ConnectNamedPipe(ctypes.c_void_p(handle), None)
        if ret:
            return True
        err = ctypes.GetLastError()
        return err == ERROR_PIPE_CONNECTED

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

    def _peek_pipe(handle) -> bool:
        """Return False if pipe is broken."""
        avail = ctypes.wintypes.DWORD(0)
        _kernel32.PeekNamedPipe.restype = ctypes.wintypes.BOOL
        ok = _kernel32.PeekNamedPipe(
            ctypes.c_void_p(handle), None, 0, None,
            ctypes.byref(avail), None
        )
        return bool(ok)

    def _close_handle(handle):
        try:
            _kernel32.CloseHandle(ctypes.c_void_p(handle))
        except Exception:
            pass

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
            import time
            while self._running:
                try:
                    handle = _create_pipe_handle(CMD_PIPE_NAME)
                    if not _connect_pipe(handle):
                        _close_handle(handle)
                        time.sleep(0.1)
                        continue
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
                    data = _read_pipe(handle)
                    if data is None:
                        break
                    msg = decode_msg(data)
                    if msg is None:
                        continue
                    reply = self._on_command(msg)
                    if reply is not None:
                        if not _write_pipe(handle, encode_msg(reply)):
                            break
            finally:
                _close_handle(handle)

        def _event_loop(self):
            import time
            while self._running:
                try:
                    handle = _create_pipe_handle(EVENT_PIPE_NAME)
                    if not _connect_pipe(handle):
                        _close_handle(handle)
                        time.sleep(0.1)
                        continue
                    with self._event_lock:
                        self._event_handle = handle
                    while self._running:
                        if not _peek_pipe(handle):
                            break
                        import time as _t; _t.sleep(0.5)
                    with self._event_lock:
                        self._event_handle = None
                    _close_handle(handle)
                except Exception:
                    time.sleep(0.1)

        def push(self, event: dict):
            with self._event_lock:
                handle = self._event_handle
            if handle is None:
                return
            try:
                _write_pipe(handle, encode_msg(event))
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
