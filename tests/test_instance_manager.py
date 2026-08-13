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

    class FakeSession:
        alive = True

    inst = Instance(
        {"id": f"adb:{serial}", "title": "t", "ldplayer_index": 0},
        FakeSession(), 100, 200,
    )
    im._instances[serial] = inst

    assert im.select(serial) is True
    assert im.active is inst


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
