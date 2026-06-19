import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from server.scrcpy_session import ScrcpySession


def test_scrcpy_session_not_alive_before_start():
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    assert not s.alive


def test_scrcpy_session_stop_idempotent():
    """stop() on an unstarted session should not raise."""
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    s.stop()
    s.stop()
