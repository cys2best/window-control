import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
import time
import threading

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
    assert state.active_hwnd is None
    assert state.quality == 85
    assert state.running is False
    assert state.window_available is True

def test_capture_state_quality_set():
    state = CaptureState()
    state.quality = 40
    assert state.quality == 40
