import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from server.instance_manager import InstanceManager
from server.mediamtx_manager import MediamtxManager


def test_set_tier_unknown_serial_false():
    im = InstanceManager(MediamtxManager())
    assert im.set_tier("emulator-9999", "1080") is False


def test_select_unknown_serial_false_no_repoint():
    calls = []

    class FakeMediamtx(MediamtxManager):
        def set_active_source(self, name):
            calls.append(name)

    im = InstanceManager(FakeMediamtx())
    assert im.select("emulator-9999") is False
    assert calls == []


def test_select_known_serial_repoints():
    from server.instance_manager import Instance, instance_name

    calls = []

    class FakeMediamtx(MediamtxManager):
        def set_active_source(self, name):
            calls.append(name)

    im = InstanceManager(FakeMediamtx())
    serial = "emulator-5554"

    class FakeSession:
        alive = True  # select() only repoints a live/publishing session

    inst = Instance(
        {"id": f"adb:{serial}", "title": "t", "ldplayer_index": 0},
        FakeSession(), 100, 200,
    )
    im._instances[serial] = inst

    assert im.select(serial) is True
    assert calls == [instance_name(serial)]


def test_select_dead_session_refuses_repoint():
    # A session that never came up (alive False even after start) must not
    # repoint 'active' at a path with no publisher — that 404-storms mediamtx.
    from server.instance_manager import Instance

    calls = []

    class FakeMediamtx(MediamtxManager):
        def set_active_source(self, name):
            calls.append(name)

    im = InstanceManager(FakeMediamtx())
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
    assert calls == []
