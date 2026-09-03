import queue
import threading
import time

import pytest

from config import DEFAULT_TIER
from server.engine_admin import EngineHealth
from server.engine_orchestrator import EngineOrchestrator
from server.engine_process import EngineReadyRecord
from server.engine_runtime import EngineRuntime, EngineRuntimeConfig
from server.instance_manager import Instance, InstanceManager, instance_name
from server.scrcpy_server import ScrcpyServerLauncher


class FakeOrchestrator:
    def __init__(self, set_tier_result=False, select_result=None, events=None):
        self.add_calls = []
        self.remove_calls = []
        self.select_calls = []
        self.select_user_ids = []
        self.tier_calls = []
        self.keyframe_calls = []
        self.check_count = 0
        self.stop_count = 0
        self.set_tier_result = set_tier_result
        self.select_result = select_result
        self.events = events

    def add_instance(self, serial, instance_name, instance_index, tier):
        if self.events is not None:
            self.events.append("add")
        self.add_calls.append((serial, instance_name, instance_index, tier))

    def remove_instance(self, serial):
        self.remove_calls.append(serial)

    def select(self, serial, advertised_host, user_id=None):
        self.select_calls.append((serial, advertised_host))
        self.select_user_ids.append(user_id)
        return self.select_result

    def set_tier(self, serial, tier):
        self.tier_calls.append((serial, tier))
        return self.set_tier_result

    def request_keyframe(self, serial):
        self.keyframe_calls.append(serial)

    def check_all(self):
        if self.events is not None:
            self.events.append("check")
        self.check_count += 1
        return {}

    def stop_all(self):
        self.stop_count += 1


def make_vm(serial="emulator-5554", index=0):
    return {
        "id": f"adb:{serial}",
        "title": f"LDPlayer #{index}",
        "ldplayer_index": index,
    }


def make_instance(serial="emulator-5554", index=0):
    return Instance(make_vm(serial, index), 100, 200)


def manager_with_instance(orchestrator):
    manager = InstanceManager(orchestrator)
    manager._instances["emulator-5554"] = make_instance()
    return manager


def patch_one_discovered_vm(monkeypatch):
    monkeypatch.setattr(
        "server.instance_manager.adb_manager.list_vms", lambda: [make_vm()]
    )
    monkeypatch.setattr(
        "server.instance_manager.adb_manager.get_screen_size", lambda serial: (100, 200)
    )
    monkeypatch.setattr("server.tailscale.get_best_ip", lambda: "100.64.1.1")


def test_instance_manager_requires_one_engine_orchestrator():
    orchestrator = FakeOrchestrator()

    manager = InstanceManager(orchestrator)

    assert manager._engine_orchestrator is orchestrator
    with pytest.raises(TypeError):
        InstanceManager()
    with pytest.raises(TypeError):
        InstanceManager(None, engine_orchestrator=orchestrator)


def test_instance_is_socket_free_metadata_only():
    instance = make_instance()

    assert instance.serial == "emulator-5554"
    assert instance.name == "instance0"
    assert not hasattr(instance, "session")


def test_instance_name_is_stable_for_emulator_and_remote_serials():
    assert instance_name("emulator-5554") == "instance0"
    assert instance_name("emulator-5558") == "instance2"
    assert instance_name("192.168.1.100:5555") == "instance_192.168.1.100_5555"


def test_refresh_ensures_stun_before_adding_engine_runtime(monkeypatch):
    events = []
    orchestrator = FakeOrchestrator(events=events)
    manager = InstanceManager(orchestrator)
    patch_one_discovered_vm(monkeypatch)
    monkeypatch.setattr(
        manager, "_ensure_stun", lambda ip: events.append(("stun", ip))
    )

    manager.refresh()

    assert events == [("stun", "100.64.1.1"), "add"]
    assert orchestrator.add_calls == [
        ("emulator-5554", "instance0", 0, DEFAULT_TIER)
    ]
    assert manager.get("emulator-5554") is not None


def test_failed_engine_add_is_not_published(monkeypatch):
    class FailingOrchestrator(FakeOrchestrator):
        def add_instance(self, *args):
            raise RuntimeError("engine start failed")

    manager = InstanceManager(FailingOrchestrator())
    patch_one_discovered_vm(monkeypatch)
    monkeypatch.setattr(manager, "_ensure_stun", lambda ip: None)

    with pytest.raises(RuntimeError, match="engine start failed"):
        manager.refresh()

    assert manager.get("emulator-5554") is None


def test_device_removal_stops_engine_runtime(monkeypatch):
    orchestrator = FakeOrchestrator()
    manager = manager_with_instance(orchestrator)
    monkeypatch.setattr("server.instance_manager.adb_manager.list_vms", lambda: [])
    monkeypatch.setattr("server.tailscale.get_best_ip", lambda: "100.64.1.1")
    monkeypatch.setattr(manager, "_ensure_stun", lambda ip: None)

    manager.refresh()

    assert orchestrator.remove_calls == ["emulator-5554"]
    assert manager.get("emulator-5554") is None


def test_rediscovery_waits_for_stale_removal_before_recreating_runtime(monkeypatch):
    serial = "emulator-5554"
    vm = make_vm(serial)
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
            assert allow_remove.wait(timeout=2)
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
    manager = manager_with_instance(orchestrator)
    manager._engine_refresh_lock = ObservedLock()
    monkeypatch.setattr("server.instance_manager.adb_manager.list_vms", list_vms)
    monkeypatch.setattr(
        "server.instance_manager.adb_manager.get_screen_size", lambda serial: (100, 200)
    )
    monkeypatch.setattr("server.tailscale.get_best_ip", lambda: "100.64.1.1")
    monkeypatch.setattr(manager, "_ensure_stun", lambda ip: None)
    errors = []

    def refresh():
        try:
            manager.refresh()
        except BaseException as error:
            errors.append(error)

    real_start = threading.Thread.start
    stale_refresh = threading.Thread(target=refresh)
    rediscovery_refresh = threading.Thread(target=refresh)
    real_start(stale_refresh)
    try:
        assert remove_entered.wait(timeout=2)
        real_start(rediscovery_refresh)
        assert interleaving.get(timeout=2) == "serialized"
    finally:
        allow_remove.set()

    stale_refresh.join(timeout=2)
    rediscovery_refresh.join(timeout=2)
    assert not stale_refresh.is_alive()
    assert not rediscovery_refresh.is_alive()
    assert errors == []
    assert orchestrator.runtimes == {serial}
    assert manager.get(serial) is not None


def test_stun_binding_is_reused_then_rebound_when_ip_changes(monkeypatch):
    servers = []

    class FakeStunServer:
        def __init__(self, host, port):
            self.host = host
            self.port = port
            self.starts = 0
            self.stops = 0
            servers.append(self)

        def start(self):
            self.starts += 1

        def stop(self):
            self.stops += 1

    monkeypatch.setattr("server.instance_manager.StunServer", FakeStunServer)
    manager = InstanceManager(FakeOrchestrator())

    manager._ensure_stun("100.64.1.1")
    manager._ensure_stun("100.64.1.1")
    manager._ensure_stun("100.64.1.2")

    assert [(server.host, server.starts, server.stops) for server in servers] == [
        ("100.64.1.1", 1, 1),
        ("100.64.1.2", 1, 0),
    ]


def test_selection_marks_active_only_after_fresh_success():
    first_selection = object()
    orchestrator = FakeOrchestrator(select_result=first_selection)
    manager = manager_with_instance(orchestrator)

    assert manager.select("emulator-5554", "100.64.1.4") is first_selection
    assert orchestrator.select_calls == [("emulator-5554", "100.64.1.4")]
    assert manager.active is manager.get("emulator-5554")

    orchestrator.select_result = None
    assert manager.select("emulator-5554", "100.64.1.5") is None
    assert manager.active is manager.get("emulator-5554")


def test_select_forwards_user_id_to_orchestrator():
    orchestrator = FakeOrchestrator(select_result=object())
    manager = manager_with_instance(orchestrator)

    manager.select("emulator-5554", "100.64.1.4", user_id="user-42")

    assert orchestrator.select_user_ids == ["user-42"]


def test_quality_and_keyframe_delegate_to_engine_orchestrator():
    orchestrator = FakeOrchestrator(set_tier_result=True)
    manager = manager_with_instance(orchestrator)

    assert manager.set_tier("emulator-5554", "1080") is True
    assert manager.set_tier("emulator-9999", "1080") is False
    manager.request_keyframe("emulator-5554")

    assert orchestrator.tier_calls == [("emulator-5554", "1080")]
    assert orchestrator.keyframe_calls == ["emulator-5554"]
    assert manager.get("emulator-5554").tier == "1080"


def test_watchdog_refreshes_before_checking_health(monkeypatch):
    class EndWatchdog(Exception):
        pass

    events = []
    orchestrator = FakeOrchestrator(events=events)
    manager = InstanceManager(orchestrator)
    monkeypatch.setattr(manager, "refresh", lambda: events.append("refresh"))
    intervals = 0

    def one_interval(_seconds):
        nonlocal intervals
        intervals += 1
        if intervals > 1:
            raise EndWatchdog

    monkeypatch.setattr("server.instance_manager.time.sleep", one_interval)

    with pytest.raises(EndWatchdog):
        manager._watchdog()

    assert events == ["refresh", "check"]


def test_watchdog_removal_waits_for_recovery_then_stops_engine_and_forward(monkeypatch):
    serial = "emulator-5554"
    forward_removals = []
    recovery_entered = threading.Event()
    release_recovery = threading.Event()

    class Engine:
        running = True
        stop_count = 0

        def start(self):
            return EngineReadyRecord("instance0", 4242, 51000, 51001, 0, 1280, 720)

        def is_running(self):
            return self.running

        def stop(self):
            self.stop_count += 1
            self.running = False

    class Admin:
        def health(self, _admin_port):
            recovery_entered.set()
            assert release_recovery.wait(timeout=5)
            return EngineHealth("stalled", 0, 1280, 720, 0, False)

        def reconnect(self, _admin_port, _scrcpy_port, generation):
            return generation

        def keyframe(self, _admin_port):
            pass

    class TokenIssuer:
        def whep(self, _instance_name):
            return "whep"

        def signaling(self, _instance_name, _role):
            return "signal"

    engine = Engine()
    launcher = ScrcpyServerLauncher(
        serial,
        0,
        find_adb=lambda: "adb",
        start_server=lambda *_args: True,
        stop_server=lambda *args: forward_removals.append(args),
    )
    config = EngineRuntimeConfig(
        exe_path=r"C:\engine\engine.exe",
        whep_secret="whep-secret",
        signaling_url="",
        signaling_secret="signal-secret",
        local_ice_servers=(),
        public_ice_servers=(),
    )

    def runtime_factory(*args):
        return EngineRuntime(
            *args[:5],
            launcher,
            Admin(),
            TokenIssuer(),
            engine_factory=lambda **_engine_args: engine,
            stall_grace_seconds=0,
            log=lambda _message: None,
        )

    orchestrator = EngineOrchestrator(
        config, runtime_factory=runtime_factory, log=lambda _: None
    )
    orchestrator.add_instance(serial, "instance0", 0, DEFAULT_TIER)
    manager = manager_with_instance(orchestrator)
    monkeypatch.setattr("server.instance_manager.adb_manager.list_vms", lambda: [])
    monkeypatch.setattr("server.tailscale.get_best_ip", lambda: "100.64.1.1")
    monkeypatch.setattr(manager, "_ensure_stun", lambda ip: None)

    real_start = threading.Thread.start
    recovery = threading.Thread(target=orchestrator.check_all)
    real_start(recovery)
    assert recovery_entered.wait(timeout=5)

    class EndWatchdog(Exception):
        pass

    intervals = 0
    watchdog_thread = None
    real_sleep = time.sleep

    def one_interval_then_stop(_seconds):
        nonlocal intervals
        if threading.current_thread() is not watchdog_thread:
            return real_sleep(_seconds)
        intervals += 1
        if intervals > 1:
            raise EndWatchdog

    monkeypatch.setattr("server.instance_manager.time.sleep", one_interval_then_stop)
    watchdog_errors = []

    def watchdog():
        try:
            manager._watchdog()
        except EndWatchdog:
            pass
        except BaseException as error:
            watchdog_errors.append(error)

    watchdog_thread = threading.Thread(target=watchdog)
    real_start(watchdog_thread)
    watchdog_thread.join(timeout=0.2)
    assert watchdog_thread.is_alive()

    release_recovery.set()
    recovery.join(timeout=5)
    watchdog_thread.join(timeout=5)

    assert not recovery.is_alive()
    assert not watchdog_thread.is_alive()
    assert watchdog_errors == []
    assert engine.stop_count == 1
    assert forward_removals[-1] == ("adb", serial, 27183, 0)
    assert orchestrator.select(serial, "127.0.0.1") is None


def test_stop_all_cleans_runtime_registry_and_stun_binding():
    class FakeStun:
        stop_count = 0

        def stop(self):
            self.stop_count += 1

    orchestrator = FakeOrchestrator()
    manager = manager_with_instance(orchestrator)
    stun = FakeStun()
    manager._stun = stun
    manager._stun_ip = "100.64.1.1"
    manager._active_serial = "emulator-5554"

    manager.stop_all()

    assert orchestrator.stop_count == 1
    assert manager.list_instances() == []
    assert manager.active is None
    assert stun.stop_count == 1
    assert manager._stun is None
    assert manager._stun_ip is None


@pytest.mark.parametrize(
    "removed_name",
    [
        "set_webrtc_manager",
        "engine_enabled",
        "select_engine",
        "get_by_name",
        "start_video",
        "stop_video",
    ],
)
def test_legacy_manager_interfaces_are_absent(removed_name):
    assert not hasattr(InstanceManager, removed_name)
