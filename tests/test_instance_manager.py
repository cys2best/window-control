import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from server.instance_manager import InstanceManager
from server.mediamtx_manager import MediamtxManager


def test_set_tier_unknown_serial_false():
    im = InstanceManager(MediamtxManager())
    assert im.set_tier("emulator-9999", "1080") is False


def test_select_unknown_serial_false():
    im = InstanceManager(MediamtxManager())
    assert im.select("emulator-9999") is False
    assert im.active is None


def test_select_known_serial_marks_active():
    # Option B: select() no longer repoints a mux; it just records which live
    # instance is active for input routing. The browser WHEPs to instanceN.
    from server.instance_manager import Instance

    im = InstanceManager(MediamtxManager())
    serial = "emulator-5554"

    class FakeControl:
        def request_idr(self):
            pass

    class FakeSession:
        alive = True
        control = FakeControl()

    inst = Instance(
        {"id": f"adb:{serial}", "title": "t", "ldplayer_index": 0},
        FakeSession(), 100, 200,
    )
    im._instances[serial] = inst

    assert im.select(serial) is True
    assert im.active is inst


def test_select_requests_idr_for_instant_switch():
    # Copy-mux has no ffmpeg GOP; keyframes come from the ~2s IDR heartbeat. On a
    # switch the browser WHEPs to the target path and can't render until it sees
    # an IDR, so without this it waits up to ~2s (black screen). select() forces
    # one immediately so the switch is near-instant.
    from server.instance_manager import Instance

    im = InstanceManager(MediamtxManager())
    serial = "emulator-5554"
    idr_calls = {"n": 0}

    class FakeControl:
        def request_idr(self):
            idr_calls["n"] += 1

    class FakeSession:
        alive = True
        control = FakeControl()

    inst = Instance(
        {"id": f"adb:{serial}", "title": "t", "ldplayer_index": 0},
        FakeSession(), 100, 200,
    )
    im._instances[serial] = inst

    assert im.select(serial) is True
    assert idr_calls["n"] == 1


def test_select_dead_session_refused():
    # A session that never comes up (alive False even after start) can't be the
    # WHEP target — select refuses so the client never WHEPs a sourceless path.
    from server.instance_manager import Instance

    im = InstanceManager(MediamtxManager())
    serial = "emulator-5554"

    class DeadSession:
        alive = False

        def start(self):
            return False

    inst = Instance(
        {"id": f"adb:{serial}", "title": "t", "ldplayer_index": 0},
        DeadSession(), 100, 200,
    )
    im._instances[serial] = inst

    assert im.select(serial) is False
    assert im.active is None
