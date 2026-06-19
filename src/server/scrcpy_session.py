"""
ScrcpySession: captures H.264 from a LDPlayer instance via ADB screenrecord,
pipes raw H.264 to ffmpeg which pushes an RTSP stream to mediamtx.

Pipeline:
  adb exec-out screenrecord --output-format=h264 --time-limit=3600 -
    →  ffmpeg -f h264 -i pipe:0 -c:v copy -f rtsp <rtsp_url>
    →  mediamtx RTSP path
    →  mediamtx serves WebRTC/WHEP to iPhone
"""

import os
import subprocess
import sys
import threading
import traceback


def _log(msg: str):
    for _p in [r"C:\ProgramData\WindowControl", r"C:\Windows\Temp"]:
        try:
            os.makedirs(_p, exist_ok=True)
            with open(os.path.join(_p, "service_crash.log"), "a") as f:
                f.write(msg + "\n")
            return
        except Exception:
            continue


def _no_window_flags():
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}
    return {}


def _find_adb() -> str | None:
    from server.adb_manager import _find_adb as adb_find
    return adb_find()


def _get_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


class ScrcpySession:
    """Manages one ADB screenrecord capture + ffmpeg RTSP push for one LDPlayer instance."""

    def __init__(self, serial: str, instance_index: int, rtsp_url: str,
                 w: int, h: int):
        self.serial = serial
        self.instance_index = instance_index
        self.rtsp_url = rtsp_url
        self.w = w
        self.h = h
        self._record_proc: subprocess.Popen | None = None
        self._ffmpeg_proc: subprocess.Popen | None = None
        self._running = False
        self._lock = threading.Lock()

    def start(self) -> bool:
        adb = _find_adb()
        ffmpeg_exe = _get_ffmpeg()
        if not adb:
            _log(f"[scrcpy] adb not found serial={self.serial}")
            return False
        if not ffmpeg_exe:
            _log(f"[scrcpy] ffmpeg not found serial={self.serial}")
            return False

        with self._lock:
            self._stop_locked()
            self._running = True
            try:
                nw = _no_window_flags()
                self._record_proc = subprocess.Popen(
                    [
                        adb, "-s", self.serial,
                        "exec-out",
                        "screenrecord", "--output-format=h264",
                        "--bit-rate=4000000", "--time-limit=3600", "-",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    **nw,
                )
                self._ffmpeg_proc = subprocess.Popen(
                    [
                        ffmpeg_exe,
                        "-loglevel", "quiet",
                        "-fflags", "nobuffer",
                        "-flags", "low_delay",
                        "-probesize", "32",
                        "-analyzeduration", "0",
                        "-f", "h264",
                        "-i", "pipe:0",
                        "-c:v", "copy",
                        "-f", "rtsp",
                        "-rtsp_transport", "tcp",
                        self.rtsp_url,
                    ],
                    stdin=self._record_proc.stdout,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    **nw,
                )
                self._record_proc.stdout.close()
                _log(f"[scrcpy] started serial={self.serial} → {self.rtsp_url}")
            except Exception:
                _log(f"[scrcpy] start failed serial={self.serial}: {traceback.format_exc()[:400]}")
                self._stop_locked()
                return False

        threading.Thread(target=self._monitor, daemon=True).start()
        return True

    def _monitor(self):
        """Wait for screenrecord to exit, log stderr, mark session dead."""
        proc = self._record_proc
        if proc:
            try:
                _, stderr_bytes = proc.communicate(timeout=3700)
                stderr_text = (stderr_bytes or b"").decode("utf-8", errors="replace").strip()
                if stderr_text:
                    _log(f"[scrcpy] adb stderr serial={self.serial}: {stderr_text[:400]}")
            except Exception:
                try:
                    proc.wait()
                except Exception:
                    pass
        _log(f"[scrcpy] process exited serial={self.serial}")
        with self._lock:
            self._running = False

    def stop(self):
        with self._lock:
            self._stop_locked()

    def _stop_locked(self):
        """Must be called with self._lock held."""
        self._running = False
        for proc in [self._ffmpeg_proc, self._record_proc]:
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._ffmpeg_proc = None
        self._record_proc = None
        _log(f"[scrcpy] stopped serial={self.serial}")

    @property
    def alive(self) -> bool:
        with self._lock:
            return (
                self._running
                and self._record_proc is not None
                and self._record_proc.poll() is None
            )
