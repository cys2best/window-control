"""Append-only diagnostic log that survives a windowed (no console) build.

Writes to %PROGRAMDATA%\\EmuCtrl\\service_crash.log on Windows, falling back
to the user temp dir; off Windows it goes straight to the temp dir. The file
rotates so a long-running host cannot fill the disk. Writing never raises:
diagnostics must not take the app down.
"""

import logging
import logging.handlers
import os
import sys
import tempfile
import threading

FILE_NAME = "service_crash.log"
MAX_BYTES = 1_000_000
BACKUP_COUNT = 2


def default_dirs() -> list[str]:
    temp_dir = os.path.join(tempfile.gettempdir(), "EmuCtrl")
    if sys.platform == "win32":
        program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return [os.path.join(program_data, "EmuCtrl"), temp_dir]
    return [temp_dir]


class CrashLog:
    def __init__(self, dirs: list[str], max_bytes: int = MAX_BYTES,
                 backup_count: int = BACKUP_COUNT):
        self._dirs = dirs
        self._max_bytes = max_bytes
        self._backup_count = backup_count
        self._lock = threading.Lock()
        self._handler: logging.Handler | None = None
        self._opened = False

    def _open(self) -> logging.Handler | None:
        for directory in self._dirs:
            try:
                os.makedirs(directory, exist_ok=True)
                handler = logging.handlers.RotatingFileHandler(
                    os.path.join(directory, FILE_NAME),
                    maxBytes=self._max_bytes,
                    backupCount=self._backup_count,
                    encoding="utf-8",
                )
            except OSError:
                continue
            handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
            return handler
        return None

    def write(self, msg: str) -> None:
        with self._lock:
            if not self._opened:
                self._handler = self._open()
                self._opened = True
            handler = self._handler
        if handler is None:
            return
        handler.handle(logging.makeLogRecord({"msg": msg, "args": None}))


_default = CrashLog(default_dirs())


def log(msg: str) -> None:
    _default.write(msg)
