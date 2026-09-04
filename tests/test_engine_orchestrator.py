"""Registry-level lifecycle tests for discovery-managed engine runtimes."""

import threading

from server.engine_orchestrator import EngineOrchestrator
from server.engine_runtime import EngineRuntimeConfig


class FakeRuntime:
    def __init__(self):
        self.start_count = 0
        self.stop_count = 0
        self.set_tier_calls: list[str] = []
        self.keyframe_count = 0
        self.check_count = 0
        self.select_count = 0
        self.selected_hosts: list[str] = []
        self.selected_user_ids: list[str | None] = []
        self.ready = False
        self.start_error: BaseException | None = None
        self.check_error: BaseException | None = None
        self.check_result = "healthy"
        self.selection = object()

    def start(self) -> None:
        self.start_count += 1
        if self.start_error is not None:
            error, self.start_error = self.start_error, None
            raise error
        self.ready = True

    def stop(self) -> None:
        self.stop_count += 1
        self.ready = False

    def select(self, advertised_host: str, user_id: str | None = None):
        self.select_count += 1
        self.selected_hosts.append(advertised_host)
        self.selected_user_ids.append(user_id)
        return self.selection if self.ready else None

    def set_tier(self, tier: str) -> None:
        self.set_tier_calls.append(tier)

    def request_keyframe(self) -> None:
        self.keyframe_count += 1

    def check_once(self) -> str:
        self.check_count += 1
        if self.check_error is not None:
            error, self.check_error = self.check_error, None
            raise error
        if not self.ready:
            self.start()
            return "respawned"
        return self.check_result


class FakeRuntimeFactory:
    def __init__(self):
        self.runtimes: list[FakeRuntime] = []
        self.calls: list[tuple] = []
        self.next_start_error: BaseException | None = None

    @property
    def created_count(self) -> int:
        return len(self.runtimes)

    def __call__(self, *args) -> FakeRuntime:
        self.calls.append(args)
        runtime = FakeRuntime()
        runtime.start_error, self.next_start_error = self.next_start_error, None
        self.runtimes.append(runtime)
        return runtime


def make_config() -> EngineRuntimeConfig:
    return EngineRuntimeConfig(
        exe_path=r"C:\engine\engine.exe",
        whep_secret="whep-secret",
        signaling_url="wss://signal.example",
        signaling_secret="signaling-secret",
        local_ice_servers=("stun:100.64.1.4:3478",),
        public_ice_servers=("stun:vps.example:3478",),
    )


def make_orchestrator():
    factory = FakeRuntimeFactory()
    return (
        EngineOrchestrator(make_config(), runtime_factory=factory, log=lambda _: None),
        factory,
    )


def test_add_starts_immediately_and_duplicate_add_is_idempotent():
    orchestrator, factory = make_orchestrator()

    orchestrator.add_instance("emulator-5554", "instance0", 0, "720")
    orchestrator.add_instance("emulator-5554", "instance0", 0, "720")

    assert factory.created_count == 1
    assert factory.runtimes[0].start_count == 1


def test_add_constructs_each_runtime_with_shared_clients_and_own_launcher():
    orchestrator, factory = make_orchestrator()

    orchestrator.add_instance("emulator-5554", "instance0", 0, "720")
    orchestrator.add_instance("emulator-5556", "instance1", 1, "1080")

    first, second = factory.calls
    assert first[:4] == ("emulator-5554", "instance0", 0, "720")
    assert second[:4] == ("emulator-5556", "instance1", 1, "1080")
    assert first[4] is orchestrator.config
    assert second[4] is orchestrator.config
    assert first[5] is not second[5]
    assert first[6] is second[6]
    assert first[7] is second[7]


def test_failed_initial_start_stays_registered_and_check_all_retries_it():
    orchestrator, factory = make_orchestrator()
    # The fake consumes this failure, so its first watchdog retry can recover.
    factory.next_start_error = RuntimeError("engine unavailable")

    orchestrator.add_instance("emulator-5554", "instance0", 0, "720")

    assert orchestrator.select("emulator-5554", "127.0.0.1") is None
    assert orchestrator.check_all() == {"emulator-5554": "respawned"}
    assert factory.runtimes[0].start_count == 2


def test_remove_during_recovery_stops_and_forgets_runtime():
    orchestrator, factory = make_orchestrator()
    orchestrator.add_instance("emulator-5554", "instance0", 0, "720")

    orchestrator.remove_instance("emulator-5554")

    assert factory.runtimes[0].stop_count == 1
    assert orchestrator.select("emulator-5554", "127.0.0.1") is None


def test_unknown_serial_operations_are_noops():
    orchestrator, factory = make_orchestrator()
    orchestrator.add_instance("emulator-5554", "instance0", 0, "720")

    orchestrator.remove_instance("unknown")
    orchestrator.request_keyframe("unknown")

    assert orchestrator.select("unknown", "127.0.0.1") is None
    assert orchestrator.set_tier("unknown", "1080") is False
    assert factory.runtimes[0].keyframe_count == 0


def test_select_delegates_host_and_returns_registered_runtime_selection():
    orchestrator, factory = make_orchestrator()
    orchestrator.add_instance("emulator-5554", "instance0", 0, "720")

    selection = orchestrator.select("emulator-5554", "192.0.2.10")

    assert selection is factory.runtimes[0].selection
    assert factory.runtimes[0].select_count == 1
    assert factory.runtimes[0].selected_hosts == ["192.0.2.10"]


def test_select_forwards_user_id_to_runtime():
    orchestrator, factory = make_orchestrator()
    orchestrator.add_instance("emulator-5554", "instance0", 0, "720")

    orchestrator.select("emulator-5554", "192.0.2.10", user_id="user-42")

    assert factory.runtimes[0].selected_user_ids == ["user-42"]


def test_tier_and_keyframe_delegate_to_the_registered_runtime():
    orchestrator, factory = make_orchestrator()
    orchestrator.add_instance("emulator-5554", "instance0", 0, "720")

    assert orchestrator.set_tier("emulator-5554", "1080") is True
    orchestrator.request_keyframe("emulator-5554")

    assert factory.runtimes[0].set_tier_calls == ["1080"]
    assert factory.runtimes[0].keyframe_count == 1


def test_check_all_isolates_runtime_failures():
    orchestrator, factory = make_orchestrator()
    orchestrator.add_instance("emulator-5554", "instance0", 0, "720")
    orchestrator.add_instance("emulator-5556", "instance1", 1, "720")
    factory.runtimes[0].check_error = RuntimeError("admin unavailable")

    assert orchestrator.check_all() == {
        "emulator-5554": "degraded",
        "emulator-5556": "healthy",
    }
    assert factory.runtimes[1].check_count == 1


def test_stop_all_stops_every_runtime_and_clears_the_registry():
    orchestrator, factory = make_orchestrator()
    orchestrator.add_instance("emulator-5554", "instance0", 0, "720")
    orchestrator.add_instance("emulator-5556", "instance1", 1, "720")

    orchestrator.stop_all()

    assert [runtime.stop_count for runtime in factory.runtimes] == [1, 1]
    assert orchestrator.select("emulator-5554", "127.0.0.1") is None
    assert orchestrator.select("emulator-5556", "127.0.0.1") is None


def test_stop_all_closes_registry_and_stops_an_add_that_arrives_late():
    factory = FakeRuntimeFactory()
    factory_entered = threading.Event()
    release_factory = threading.Event()

    def blocking_factory(*args):
        runtime = factory(*args)
        factory_entered.set()
        release_factory.wait(timeout=2)
        return runtime

    orchestrator = EngineOrchestrator(
        make_config(), runtime_factory=blocking_factory, log=lambda _: None
    )
    worker = threading.Thread(
        target=lambda: orchestrator.add_instance(
            "emulator-5554", "instance0", 0, "720"
        )
    )
    worker.start()

    assert factory_entered.wait(timeout=1)
    orchestrator.stop_all()
    release_factory.set()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert factory.created_count == 1
    assert factory.runtimes[0].start_count == 0
    assert factory.runtimes[0].stop_count == 1
    assert orchestrator.select("emulator-5554", "127.0.0.1") is None

    orchestrator.add_instance("emulator-5556", "instance1", 1, "720")
    assert factory.created_count == 1
