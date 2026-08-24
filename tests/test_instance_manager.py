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


def test_refresh_uses_incremental_paths_when_mediamtx_already_running(monkeypatch):
    # A full mediamtx.start() restart tears down every other instance's live
    # WHEP stream, so refresh() must patch paths via add_path/remove_path
    # once the process is up, and only call start() to boot it the first time.
    from server import instance_manager as im_mod

    monkeypatch.setattr(im_mod, "adb_manager", type("M", (), {
        "list_vms": staticmethod(lambda: [
            {"id": "adb:emulator-5554", "title": "t", "ldplayer_index": 0}
        ]),
        "get_screen_size": staticmethod(lambda serial: (100, 200)),
    }))
    monkeypatch.setattr("server.tailscale.get_best_ip", lambda: "100.64.1.1")
    monkeypatch.setattr(im_mod.ScrcpySession, "start", lambda self: True)

    class FakeMediamtx:
        running = True

        def __init__(self):
            self.started = []
            self.added = []
            self.removed = []

        def start(self, names, tailscale_ip=None):
            self.started.append((list(names), tailscale_ip))

        def add_path(self, name):
            self.added.append(name)
            return True

        def remove_path(self, name):
            self.removed.append(name)
            return True

        def rtsp_url(self, name):
            return f"rtsp://localhost/{name}"

    mediamtx = FakeMediamtx()
    im = InstanceManager(mediamtx)
    monkeypatch.setattr(im, "_ensure_stun", lambda ip: None)

    im.refresh()

    assert mediamtx.started == []  # process already running — no full restart
    assert mediamtx.added == ["instance0"]
    assert mediamtx.removed == []


def test_get_by_name_known_returns_instance():
    from server.instance_manager import Instance

    im = InstanceManager(MediamtxManager())
    serial = "emulator-5554"
    inst = Instance(
        {"id": f"adb:{serial}", "title": "t", "ldplayer_index": 0},
        object(), 100, 200,
    )
    im._instances[serial] = inst

    assert im.get_by_name("instance0") is inst


def test_get_by_name_unknown_returns_none():
    im = InstanceManager(MediamtxManager())
    assert im.get_by_name("instance99") is None


def test_start_video_by_name_delegates_to_session():
    from server.instance_manager import Instance

    im = InstanceManager(MediamtxManager())
    serial = "emulator-5554"
    calls = {"n": 0}

    class FakeSession:
        def start_video(self):
            calls["n"] += 1
            return True

    inst = Instance(
        {"id": f"adb:{serial}", "title": "t", "ldplayer_index": 0},
        FakeSession(), 100, 200,
    )
    im._instances[serial] = inst

    assert im.start_video("instance0") is True
    assert calls["n"] == 1


def test_start_video_unknown_name_returns_false():
    im = InstanceManager(MediamtxManager())
    assert im.start_video("instance99") is False


def test_stop_video_by_name_delegates_to_session():
    from server.instance_manager import Instance

    im = InstanceManager(MediamtxManager())
    serial = "emulator-5554"
    calls = {"n": 0}

    class FakeSession:
        def stop_video(self):
            calls["n"] += 1

    inst = Instance(
        {"id": f"adb:{serial}", "title": "t", "ldplayer_index": 0},
        FakeSession(), 100, 200,
    )
    im._instances[serial] = inst

    im.stop_video("instance0")
    assert calls["n"] == 1


def test_stop_video_unknown_name_is_noop():
    im = InstanceManager(MediamtxManager())
    im.stop_video("instance99")  # must not raise
