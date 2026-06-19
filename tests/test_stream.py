import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from server.stream import FrameQueue, CaptureState


def test_frame_queue_drop_oldest():
    q = FrameQueue(maxsize=2)
    q.put(b"frame1")
    q.put(b"frame2")
    q.put(b"frame3")  # should drop frame1
    assert q.get() == b"frame2"
    assert q.get() == b"frame3"


def test_capture_state_defaults():
    state = CaptureState()
    assert state.adb_session is None
    assert state.quality == 85
    assert state.running is False
    assert state.frames_served == 0


def test_capture_state_quality_set():
    state = CaptureState()
    state.set_quality(40)
    assert state.quality == 40


def test_capture_state_set_adb_session_stops_old():
    from unittest.mock import MagicMock
    state = CaptureState()
    old = MagicMock()
    state.set_adb_session(old)
    new = MagicMock()
    state.set_adb_session(new)
    old.stop.assert_called_once()
    assert state.adb_session is new


def test_capture_state_clear_adb_session():
    from unittest.mock import MagicMock
    state = CaptureState()
    sess = MagicMock()
    state.set_adb_session(sess)
    state.clear_adb_session()
    sess.stop.assert_called_once()
    assert state.adb_session is None
