import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import queue
import threading

import pytest

from config import DEFAULT_TIER
from server.instance_manager import Instance, InstanceManager
from server.mediamtx_manager import MediamtxManager


class FakeOrchestrator:
    def __init__(self, set_tier_result=False, select_result=None):
        self.add_calls = []
        self.remove_calls = []
        self.select_calls = []
        self.tier_calls = []
        self.keyframe_calls = []
        self.check_count = 0
        self.stop_count = 0
        self.set_tier_result = set_tier_result
        self.select_result = select_result

    def add_instance(self, serial, instance_name, instance_index, tier):
        self.add_calls.append((serial, instance_name, instance_index, tier))

    def remove_instance(self, serial):
        self.remove_calls.append(serial)

    def select(self, serial, advertised_host):
        self.select_calls.append((serial, advertised_host))
        return self.select_result

    def set_tier(self, serial, tier):
        self.tier_calls.append((serial, tier))
        return self.set_tier_result

    def request_keyframe(self, serial):
        self.keyframe_calls.append(serial)

    def check_all(self):
        self.check_count += 1
        return {}

    def stop_all(self):
        self.stop_count += 1


def manager_with_engine_instance(orchestrator):
    manager = InstanceManager(None, engine_orchestrator=orchestrator)
    manager._instances["emulator-5554"] = Instance(
        {
            "id": "adb:emulator-5554",
            "title": "LDPlayer #0",
            "ldplayer_index": 0,
        },
        None,
        100,
        200,
    )
    return manager


def patch_one_discovered_vm(monkeypatch):
    monkeypatch.setattr(
        "server.instance_manager.adb_manager.list_vms",
        lambda: [
            {
                "id": "adb:emulator-5554",
                "title": "LDPlayer #0",
                "ldplayer_index": 0,
            }
        ],
    )
    monkeypatch.setattr(
        "server.instance_manager.adb_manager.get_screen_size",
        lambda serial: (100, 200),
    )
    monkeypatch.setattr(
        InstanceManager,
        "_ensure_stun",
        lambda self, ip: pytest.fail("engine discovery must return before STUN setup"),
    )


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


def test_start_video_routes_to_aiortc_when_webrtc_manager_present():
    from server.instance_manager import Instance

    calls = []

    class FakeWebrtcManager:
        def push_nalu_threadsafe(self, name, nalu):
            pass

    class FakeSession:
        alive = True

        class control:
            @staticmethod
            def request_idr():
                pass

        def start_video_aiortc(self, on_frame):
            calls.append("aiortc")
            return True

        def start_video(self):
            calls.append("ffmpeg")
            return True

    webrtc = FakeWebrtcManager()
    im = InstanceManager(mediamtx=None, webrtc=webrtc)
    inst = Instance(
        {"id": "adb:emulator-5554", "title": "t", "ldplayer_index": 0},
        FakeSession(), 100, 200,
    )
    im._instances["emulator-5554"] = inst

    ok = im.start_video(inst.name)
    assert ok is True
    assert calls == ["aiortc"]


def test_engine_discovery_starts_engine_without_constructing_scrcpy_session(monkeypatch):
    orchestrator = FakeOrchestrator()
    manager = InstanceManager(None, engine_orchestrator=orchestrator)
    patch_one_discovered_vm(monkeypatch)
    monkeypatch.setattr(
        "server.instance_manager.ScrcpySession",
        lambda *args: pytest.fail("engine mode must not create legacy socket consumer"),
    )
    manager.refresh()
    assert orchestrator.add_calls == [
        ("emulator-5554", "instance0", 0, DEFAULT_TIER)
    ]


def test_engine_device_removal_stops_runtime(monkeypatch):
    orchestrator = FakeOrchestrator()
    manager = manager_with_engine_instance(orchestrator)
    monkeypatch.setattr("server.instance_manager.adb_manager.list_vms", lambda: [])
    manager.refresh()
    assert orchestrator.remove_calls == ["emulator-5554"]


def test_engine_rediscovery_recreates_runtime_after_stale_removal(monkeypatch):
    serial = "emulator-5554"
    vm = {
        "id": f"adb:{serial}",
        "title": "LDPlayer #0",
        "ldplayer_index": 0,
    }
    remove_entered = threading.Event()
    allow_remove = threading.Event()
    interleaving = queue.Queue()

    class ObservedLock:
        def __init__(self):
            self._lock = threading.Lock()
            self._attempt_lock = threading.Lock()
            self._attempts = 0

        def __enter__(self):
            with self._attempt_lock:
                self._attempts += 1
                if self._attempts == 2:
                    interleaving.put("serialized")
            self._lock.acquire()

        def __exit__(self, exc_type, exc_value, traceback):
            self._lock.release()

    class RacingOrchestrator(FakeOrchestrator):
        def __init__(self):
            super().__init__()
            self.runtimes = {serial}

        def remove_instance(self, removed_serial):
            self.remove_calls.append(removed_serial)
            remove_entered.set()
            if not allow_remove.wait(timeout=2):
                raise AssertionError("test did not release stale removal")
            self.runtimes.discard(removed_serial)

        def add_instance(self, added_serial, name, instance_index, tier):
            self.add_calls.append((added_serial, name, instance_index, tier))
            if added_serial in self.runtimes:
                interleaving.put("raced")
                return
            self.runtimes.add(added_serial)

    list_lock = threading.Lock()
    list_call_count = 0

    def list_vms():
        nonlocal list_call_count
        with list_lock:
            list_call_count += 1
            return [] if list_call_count == 1 else [vm]

    orchestrator = RacingOrchestrator()
    manager = manager_with_engine_instance(orchestrator)
    manager._engine_refresh_lock = ObservedLock()
    monkeypatch.setattr("server.instance_manager.adb_manager.list_vms", list_vms)
    monkeypatch.setattr(
        "server.instance_manager.adb_manager.get_screen_size",
        lambda discovered_serial: (100, 200),
    )
    errors = []

    def refresh():
        try:
            manager.refresh()
        except BaseException as error:
            errors.append(error)

    stale_refresh = threading.Thread(target=refresh)
    rediscovery_refresh = None
    stale_refresh.start()
    try:
        assert remove_entered.wait(timeout=2)
        rediscovery_refresh = threading.Thread(target=refresh)
        rediscovery_refresh.start()
        interleaving.get(timeout=2)
    finally:
        allow_remove.set()

    stale_refresh.join(timeout=2)
    rediscovery_refresh.join(timeout=2)
    assert not stale_refresh.is_alive()
    assert not rediscovery_refresh.is_alive()
    assert errors == []
    assert orchestrator.runtimes == {serial}
    assert manager.get(serial) is not None


def test_engine_quality_and_keyframe_delegate_to_orchestrator():
    orchestrator = FakeOrchestrator(set_tier_result=True)
    manager = manager_with_engine_instance(orchestrator)
    assert manager.set_tier("emulator-5554", "1080") is True
    manager.request_keyframe("emulator-5554")
    assert orchestrator.tier_calls == [("emulator-5554", "1080")]
    assert orchestrator.keyframe_calls == ["emulator-5554"]


def test_engine_selection_reports_enabled_and_delegates():
    selection = object()
    orchestrator = FakeOrchestrator(select_result=selection)
    manager = manager_with_engine_instance(orchestrator)

    assert manager.engine_enabled() is True
    assert manager.select_engine("emulator-5554", "100.64.1.4") is selection
    assert orchestrator.select_calls == [("emulator-5554", "100.64.1.4")]


def test_engine_watchdog_delegates_once_per_interval(monkeypatch):
    class EndWatchdog(Exception):
        pass

    class LegacySession:
        alive_checks = 0

        @property
        def alive(self):
            self.alive_checks += 1
            return False

    monkeypatch.setattr("server.instance_manager.threading.Thread.start", lambda self: None)
    orchestrator = FakeOrchestrator()
    manager = manager_with_engine_instance(orchestrator)
    legacy_session = LegacySession()
    manager._instances["emulator-5554"].session = legacy_session
    sleep_count = 0

    def stop_after_one_interval(_seconds):
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count > 1:
            raise EndWatchdog

    monkeypatch.setattr("server.instance_manager.time.sleep", stop_after_one_interval)
    with pytest.raises(EndWatchdog):
        manager._watchdog()

    assert orchestrator.check_count == 1
    assert legacy_session.alive_checks == 0


def test_engine_watchdog_removes_disconnected_device_while_checking_health(monkeypatch):
    class EndWatchdog(Exception):
        pass

    monkeypatch.setattr("server.instance_manager.threading.Thread.start", lambda self: None)
    orchestrator = FakeOrchestrator()
    manager = manager_with_engine_instance(orchestrator)
    monkeypatch.setattr("server.instance_manager.adb_manager.list_vms", lambda: [])
    intervals = 0

    def stop_after_one_interval(_seconds):
        nonlocal intervals
        intervals += 1
        if intervals > 1:
            raise EndWatchdog

    monkeypatch.setattr("server.instance_manager.time.sleep", stop_after_one_interval)
    with pytest.raises(EndWatchdog):
        manager._watchdog()

    assert orchestrator.remove_calls == ["emulator-5554"]
    assert orchestrator.check_count == 1


def test_engine_stop_all_delegates_cleanup():
    orchestrator = FakeOrchestrator()
    manager = manager_with_engine_instance(orchestrator)

    manager.stop_all()

    assert orchestrator.stop_count == 1
