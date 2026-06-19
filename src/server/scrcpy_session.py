"""
ScrcpySession: captures H.264 from a LDPlayer instance via scrcpy stdout pipe,
passes raw H.264 to ffmpeg which pushes an RTSP stream to mediamtx.

Pipeline:
  scrcpy.exe --output-file pipe:1  →  stdout H.264 Annex B
    →  ffmpeg -f h264 -i pipe:0 -c:v copy -f rtsp <rtsp_url>
    →  mediamtx RTSP path
    →  mediamtx serves WebRTC/WHEP to iPhone
"""

import os
import subprocess
import sys
import threading
import traceback

from config import ASSETS_DIR


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


def _scrcpy_exe() -> str:
    bundled = os.path.join(ASSETS_DIR, "scrcpy", "scrcpy.exe")
    if os.path.exists(bundled):
        return bundled
    import shutil
    found = shutil.which("scrcpy")
    if found:
        return found
    return bundled


def _get_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


class ScrcpySession:
    """Manages one scrcpy capture + ffmpeg RTSP push for one LDPlayer instance."""

    def __init__(self, serial: str, instance_index: int, rtsp_url: str,
                 w: int, h: int):
        self.serial = serial
        self.instance_index = instance_index
        self.rtsp_url = rtsp_url
        self.w = w
        self.h = h
        self._scrcpy_proc: subprocess.Popen | None = None
        self._ffmpeg_proc: subprocess.Popen | None = None
        self._running = False
        self._lock = threading.Lock()

    def start(self) -> bool:
        ffmpeg_exe = _get_ffmpeg()
        if not ffmpeg_exe:
            _log("[scrcpy] ffmpeg not found")
            return False

        with self._lock:
            self._stop_locked()
            self._running = True
            try:
                exe = _scrcpy_exe()
                # scrcpy writes raw H.264 Annex B to stdout when --output-file=pipe:1
                self._scrcpy_proc = subprocess.Popen(
                    [
                        exe,
                        "--serial", self.serial,
                        "--no-playback",
                        "--video-codec", "h264",
                        "--video-bit-rate", "4M",
                        "--max-fps", "30",
                        "--output-file", "pipe:1",
                    ],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    **_no_window_flags(),
                )
                # ffmpeg reads H.264 from scrcpy stdout, pushes RTSP to mediamtx
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
                    stdin=self._scrcpy_proc.stdout,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    **_no_window_flags(),
                )
                # Close parent's copy so ffmpeg owns the pipe end
                self._scrcpy_proc.stdout.close()
                _log(f"[scrcpy] started serial={self.serial} → {self.rtsp_url}")
            except Exception:
                _log(f"[scrcpy] start failed serial={self.serial}: {traceback.format_exc()[:400]}")
                self._stop_locked()
                return False

        # Monitor in background so alive property reflects reality
        threading.Thread(target=self._monitor, daemon=True).start()
        return True

    def _monitor(self):
        """Wait for scrcpy to exit, log stderr, then mark session dead."""
        proc = self._scrcpy_proc
        if proc:
            try:
                _, stderr_bytes = proc.communicate(timeout=300)
                stderr_text = (stderr_bytes or b"").decode("utf-8", errors="replace").strip()
                if stderr_text:
                    _log(f"[scrcpy] stderr serial={self.serial}: {stderr_text[:800]}")
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
        for proc in [self._ffmpeg_proc, self._scrcpy_proc]:
            if proc:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._ffmpeg_proc = None
        self._scrcpy_proc = None
        _log(f"[scrcpy] stopped serial={self.serial}")

    @property
    def alive(self) -> bool:
        with self._lock:
            return (
                self._running
                and self._scrcpy_proc is not None
                and self._scrcpy_proc.poll() is None
            )
