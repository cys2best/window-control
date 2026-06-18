import asyncio
import io
import queue
import threading
import time

from PIL import Image


def _log(msg: str):
    import os
    for _p in [r"C:\ProgramData\WindowControl", r"C:\Windows\Temp"]:
        try:
            os.makedirs(_p, exist_ok=True)
            with open(os.path.join(_p, "service_crash.log"), "a") as f:
                f.write(msg + "\n")
            return
        except Exception:
            continue


def _make_black_frame() -> bytes:
    img = Image.new("RGB", (1280, 720), color=(0, 0, 0))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=40)
    return buf.getvalue()


BOUNDARY = b"--frame"


class FrameQueue:
    def __init__(self, maxsize=2):
        self._q = queue.Queue(maxsize=maxsize)

    def put(self, frame: bytes):
        if self._q.full():
            try:
                self._q.get_nowait()
            except queue.Empty:
                pass
        self._q.put_nowait(frame)

    def get(self, timeout=1.0) -> bytes | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None


class CaptureState:
    def __init__(self):
        self.running: bool = False
        self.quality: int = 85
        self.adb_session = None   # AdbSession when VM selected
        self._lock = threading.Lock()
        self.frames_served: int = 0  # incremented by mjpeg_generator

    def set_quality(self, q: int):
        with self._lock:
            self.quality = q

    def set_adb_session(self, session):
        with self._lock:
            old = self.adb_session
            self.adb_session = session
        if old is not None and old is not session:
            old.stop()

    def clear_adb_session(self):
        with self._lock:
            session = self.adb_session
            self.adb_session = None
        if session is not None:
            session.stop()


def capture_loop(state: CaptureState, frame_queue: FrameQueue):
    _BLACK_FRAME = _make_black_frame()
    _log("[capture_loop] started (ADB mode)")

    while state.running:
        session = state.adb_session
        if session is None:
            frame_queue.put(_BLACK_FRAME)
            time.sleep(0.1)
            continue

        frame = session.get_latest_frame()
        if frame is None:
            frame_queue.put(_BLACK_FRAME)
            time.sleep(0.05)
        else:
            frame_queue.put(frame)
            time.sleep(1 / session.fps)

    _log("[capture_loop] stopped")


async def mjpeg_generator(frame_queue: FrameQueue, state: CaptureState | None = None):
    loop = asyncio.get_event_loop()
    while True:
        frame = await loop.run_in_executor(None, frame_queue.get, 1.0)
        if frame is None:
            continue
        if state is not None:
            state.frames_served += 1
        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
        )
