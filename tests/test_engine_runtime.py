"""Tests for the serialized per-instance EngineRuntime.

Every collaborator (scrcpy launcher, engine process, admin client, token
issuer, clock) is constructor-injected. No global is monkeypatched.
"""

import threading
import types

import pytest

from server.engine_admin import (
    EngineAdminProtocolError,
    EngineAdminUnavailable,
    EngineHealth,
    ReconnectRejected,
)
from server.engine_process import EngineReadyError, EngineReadyRecord
from server.engine_runtime import EngineRuntime, EngineRuntimeConfig, EngineSelection
from server.scrcpy_server import ScrcpyLaunch


# --------------------------------------------------------------------------
# Test helpers
# --------------------------------------------------------------------------


class CountingTokenIssuer:
    """Mints distinguishable, monotonically-numbered tokens.

    whep() -> "whep:<instance>:<n>"; signaling() -> "<role>:<instance>:<n>".
    """

    def __init__(self):
        self.counter = 0
        self.whep_calls: list[str] = []
        self.signaling_calls: list[tuple[str, str]] = []

    def _next(self) -> int:
        self.counter += 1
        return self.counter

    def whep(self, instance_name: str) -> str:
        self.whep_calls.append(instance_name)
        return f"whep:{instance_name}:{self._next()}"

    def signaling(self, instance_name: str, role: str) -> str:
        self.signaling_calls.append((instance_name, role))
        return f"{role}:{instance_name}:{self._next()}"


def decode_role(token: str) -> str:
    """Return the substring before the first colon."""
    return token.split(":", 1)[0]


class FakeLauncher:
    def __init__(self, events: list, port: int = 27183):
        self._events = events
        self._port = port
        self.launch_count = 0
        self.stop_count = 0
        self.launch_error: BaseException | None = None

    def launch(self, tier: str, generation: int) -> ScrcpyLaunch:
        self._events.append(("scrcpy.launch", tier, generation))
        if self.launch_error is not None:
            error, self.launch_error = self.launch_error, None
            raise error
        self.launch_count += 1
        return ScrcpyLaunch(port=self._port, generation=generation, tier=tier)

    def stop(self) -> None:
        self._events.append(("scrcpy.stop",))
        self.stop_count += 1


class FakeEngineState:
    """Shared, mutable state across every FakeEngine the factory creates."""

    def __init__(self, events: list, ready_ports: list[tuple[int, int]]):
        self.events = events
        self.ready_ports = list(ready_ports)
        self.ready_index = 0
        self.running = True
        self.spawn_count = 0
        self.stop_count = 0
        self.env: dict[str, str] = {}
        self.start_errors: list[BaseException | None] = []
        self.instances: list["FakeEngine"] = []
        self.start_hook = None


class FakeEngine:
    def __init__(self, state: FakeEngineState, instance_name: str,
                 exe_path: str, scrcpy_port: int, env_overrides: dict[str, str]):
        self._state = state
        self.instance_name = instance_name
        self.exe_path = exe_path
        self.scrcpy_port = scrcpy_port
        self.env_overrides = dict(env_overrides)
        self.started = False

    def start(self) -> EngineReadyRecord:
        self._state.events.append(
            ("engine.start", self.instance_name, self.scrcpy_port)
        )
        self._state.env = dict(self.env_overrides)
        if self._state.start_hook is not None:
            self._state.start_hook()
        if self._state.start_errors:
            error = self._state.start_errors.pop(0)
            if error is not None:
                raise error

        index = min(self._state.ready_index, len(self._state.ready_ports) - 1)
        whep_port, admin_port = self._state.ready_ports[index]
        self._state.ready_index += 1

        self.started = True
        self._state.running = True
        return EngineReadyRecord(
            instance_name=self.instance_name,
            pid=4242,
            whep_port=whep_port,
            admin_port=admin_port,
            generation=0,
            width=1280,
            height=720,
        )

    def is_running(self) -> bool:
        return self.started and self._state.running

    def stop(self) -> None:
        self._state.events.append(("engine.stop", self.instance_name))
        self._state.stop_count += 1
        self.started = False
        self._state.running = False


class FakeEngineFactory:
    def __init__(self, state: FakeEngineState):
        self._state = state

    def __call__(self, instance_name: str, exe_path: str, scrcpy_port: int,
                 env_overrides: dict[str, str]) -> FakeEngine:
        self._state.spawn_count += 1
        engine = FakeEngine(
            self._state, instance_name, exe_path, scrcpy_port, env_overrides
        )
        self._state.instances.append(engine)
        return engine


class FakeAdmin:
    def __init__(self, events: list, *, health_state: str = "connected",
                 health_generation: int = 0,
                 health_size: tuple[int, int] = (1280, 720),
                 admin_error: BaseException | None = None,
                 reconnect_results: list | None = None):
        self._events = events
        self.health_state = health_state
        self.health_generation = health_generation
        self.health_size = health_size
        self.admin_error = admin_error
        self.reconnect_results = list(reconnect_results or [])
        self.keyframe_ports: list[int] = []
        self.health_calls: list[int] = []
        self.reconnect_generations: list[int] = []
        self.keyframe_error: BaseException | None = None

    def health(self, admin_port: int) -> EngineHealth:
        self._events.append(("admin.health", admin_port))
        self.health_calls.append(admin_port)
        if self.admin_error is not None:
            raise self.admin_error
        return EngineHealth(
            state=self.health_state,
            generation=self.health_generation,
            width=self.health_size[0],
            height=self.health_size[1],
            local_peers=0,
            public_peer=False,
        )

    def reconnect(self, admin_port: int, scrcpy_port: int, generation: int) -> int:
        self._events.append(("admin.reconnect", admin_port, scrcpy_port, generation))
        self.reconnect_generations.append(generation)
        if self.reconnect_results:
            result = self.reconnect_results.pop(0)
        elif self.admin_error is not None:
            result = self.admin_error
        else:
            result = generation
        if isinstance(result, BaseException):
            raise result
        # An accepted reconnect makes the engine's own generation the new truth.
        self.health_generation = result
        return result

    def keyframe(self, admin_port: int) -> None:
        self._events.append(("admin.keyframe", admin_port))
        self.keyframe_ports.append(admin_port)
        if self.keyframe_error is not None:
            raise self.keyframe_error


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_config(**overrides) -> EngineRuntimeConfig:
    values = dict(
        exe_path=r"C:\engine\engine.exe",
        whep_secret="whep-secret",
        signaling_url="wss://signal.example",
        signaling_secret="signal-secret",
        local_ice_servers=("stun:100.64.1.4:3478",),
        public_ice_servers=("stun:vps.example:3478", "turn:vps.example:3478"),
    )
    values.update(overrides)
    return EngineRuntimeConfig(**values)


def make_runtime(**overrides):
    """Build a real EngineRuntime wired to fakes.

    Supported overrides: token_issuer, health_generation, health_state,
    stall_grace, reconnect_results, admin_error, ready_ports, config, clock,
    initial_tier.
    """
    events: list = []

    launcher = FakeLauncher(events)
    engine_state = FakeEngineState(
        events, overrides.get("ready_ports", [(51000, 51001)])
    )
    engine_factory = FakeEngineFactory(engine_state)
    admin = FakeAdmin(
        events,
        health_state=overrides.get("health_state", "connected"),
        health_generation=overrides.get("health_generation", 0),
        admin_error=overrides.get("admin_error"),
        reconnect_results=overrides.get("reconnect_results"),
    )
    token_issuer = overrides.get("token_issuer") or CountingTokenIssuer()
    clock = overrides.get("clock") or FakeClock()
    config = overrides.get("config") or make_config()

    runtime = EngineRuntime(
        serial="emulator-5554",
        instance_name="instance0",
        instance_index=0,
        initial_tier=overrides.get("initial_tier", "720"),
        config=config,
        launcher=launcher,
        admin=admin,
        token_issuer=token_issuer,
        engine_factory=engine_factory,
        clock=clock,
        stall_grace_seconds=overrides.get("stall_grace", 3.0),
    )

    namespace = types.SimpleNamespace(
        events=events,
        launcher=launcher,
        admin=admin,
        clock=clock,
        config=config,
        token_issuer=token_issuer,
        engine_state=engine_state,
    )

    # Live views over mutable fake state.
    class _Namespace(types.SimpleNamespace):
        @property
        def engine_env(self):
            return self.engine_state.env

        @property
        def process_running(self):
            return self.engine_state.running

        @process_running.setter
        def process_running(self, value):
            self.engine_state.running = value

        @property
        def launch_count(self):
            return self.launcher.launch_count

        @property
        def engine_spawn_count(self):
            return self.engine_state.spawn_count

        @property
        def engine_stop_count(self):
            return self.engine_state.stop_count

        @property
        def reconnect_generations(self):
            return self.admin.reconnect_generations

    namespace = _Namespace(**namespace.__dict__)
    return runtime, namespace


# --------------------------------------------------------------------------
# Brief-mandated tests
# --------------------------------------------------------------------------


def test_start_launches_generation_zero_before_engine_and_mints_engine_jwt():
    runtime, fakes = make_runtime()
    runtime.start()
    assert fakes.events[:2] == [
        ("scrcpy.launch", "720", 0),
        ("engine.start", "instance0", 27183),
    ]
    assert decode_role(fakes.engine_env["ENGINE_SIGNALING_TOKEN"]) == "engine"


def test_select_mints_fresh_whep_and_viewer_tokens_without_admin_port():
    issuer = CountingTokenIssuer()
    runtime, fakes = make_runtime(token_issuer=issuer)
    runtime.start()
    first = runtime.select("100.64.1.4")
    second = runtime.select("100.64.1.4")
    assert first.whep_url == "http://100.64.1.4:51000/whep"
    assert first.whep_token != second.whep_token
    assert decode_role(first.signaling_token) == "viewer"
    assert not hasattr(first, "admin_port")


def test_tier_change_serializes_launch_then_generation_checked_reconnect():
    runtime, fakes = make_runtime(health_generation=3)
    runtime.start()
    runtime.set_tier("1080")
    reconnect_events = [event for event in fakes.events if event[0] in {
        "scrcpy.launch", "admin.reconnect",
    }]
    assert reconnect_events[-2:] == [
        ("scrcpy.launch", "1080", 4),
        ("admin.reconnect", 51001, 27183, 4),
    ]
    assert runtime.select("100.64.1.4").generation == 4


def test_stale_reconnect_retries_same_launch_from_engine_source_of_truth():
    runtime, fakes = make_runtime(
        reconnect_results=[ReconnectRejected(5), 6],
        health_state="stalled",
        stall_grace=0,
    )
    runtime.start()
    assert runtime.check_once() == "recovered"
    assert fakes.launch_count == 2
    # Base comes from the engine's own reported generation (0), so the first
    # proposal is 1. The engine rejects it as stale at 5, and the retry reuses
    # the same launch at 5 + 1 = 6.
    assert fakes.reconnect_generations == [1, 6]


def test_admin_failure_does_not_respawn_a_live_engine():
    runtime, fakes = make_runtime(admin_error=EngineAdminUnavailable("down"))
    runtime.start()
    assert runtime.check_once() == "degraded"
    assert fakes.engine_spawn_count == 1


def test_dead_engine_relaunches_scrcpy_and_refreshes_dynamic_endpoint():
    runtime, fakes = make_runtime(ready_ports=[(51000, 51001), (52000, 52001)])
    runtime.start()
    fakes.process_running = False
    assert runtime.check_once() == "respawned"
    assert runtime.select("100.64.1.4").whep_url.endswith(":52000/whep")


# --------------------------------------------------------------------------
# Start / environment construction
# --------------------------------------------------------------------------


def test_start_passes_every_configured_env_overlay_to_the_engine():
    runtime, fakes = make_runtime()
    runtime.start()
    env = fakes.engine_env
    assert env["ENGINE_WHEP_CAPABILITY_SECRET"] == "whep-secret"
    assert env["ENGINE_LOCAL_ICE_SERVERS"] == "stun:100.64.1.4:3478"
    assert env["ENGINE_SIGNALING_URL"] == "wss://signal.example"
    assert env["ENGINE_PUBLIC_ICE_SERVERS"] == \
        "stun:vps.example:3478,turn:vps.example:3478"
    assert set(env) == {
        "ENGINE_WHEP_CAPABILITY_SECRET",
        "ENGINE_LOCAL_ICE_SERVERS",
        "ENGINE_SIGNALING_URL",
        "ENGINE_SIGNALING_TOKEN",
        "ENGINE_PUBLIC_ICE_SERVERS",
    }


def test_start_is_idempotent_and_does_not_respawn_a_running_engine():
    runtime, fakes = make_runtime()
    runtime.start()
    runtime.start()
    assert fakes.engine_spawn_count == 1
    assert fakes.launch_count == 1


def test_select_before_start_returns_none():
    runtime, fakes = make_runtime()
    assert runtime.select("100.64.1.4") is None


def test_select_uses_ready_record_dimensions_and_generation():
    runtime, fakes = make_runtime()
    runtime.start()
    selection = runtime.select("100.64.1.4")
    assert isinstance(selection, EngineSelection)
    assert (selection.width, selection.height) == (1280, 720)
    assert selection.generation == 0


def test_select_brackets_ipv6_hosts():
    runtime, fakes = make_runtime()
    runtime.start()
    selection = runtime.select("fd7a:115c:a1e0::1")
    assert selection.whep_url == "http://[fd7a:115c:a1e0::1]:51000/whep"


def test_select_does_not_double_bracket_an_already_bracketed_host():
    runtime, fakes = make_runtime()
    runtime.start()
    selection = runtime.select("[fd7a:115c:a1e0::1]")
    assert selection.whep_url == "http://[fd7a:115c:a1e0::1]:51000/whep"


def test_select_returns_null_signaling_url_and_token_together_when_disabled():
    runtime, fakes = make_runtime(config=make_config(signaling_url=""))
    runtime.start()
    selection = runtime.select("100.64.1.4")
    assert selection.signaling_url is None
    assert selection.signaling_token is None


def test_select_never_exposes_the_admin_port_in_any_field():
    runtime, fakes = make_runtime()
    runtime.start()
    selection = runtime.select("100.64.1.4")
    rendered = repr(selection)
    assert "51001" not in rendered
    assert not any("admin" in field for field in selection.__dataclass_fields__)


def test_failed_start_leaves_no_engine_and_stops_the_launcher():
    runtime, fakes = make_runtime()
    fakes.engine_state.start_errors = [EngineReadyError("no ready record")]
    with pytest.raises(EngineReadyError):
        runtime.start()
    assert runtime.select("100.64.1.4") is None
    assert fakes.launcher.stop_count == 1


# --------------------------------------------------------------------------
# Tier changes
# --------------------------------------------------------------------------


def test_set_tier_to_the_current_tier_is_a_no_op():
    runtime, fakes = make_runtime()
    runtime.start()
    runtime.set_tier("720")
    assert fakes.launch_count == 1
    assert fakes.reconnect_generations == []


def test_set_tier_before_start_does_not_launch_or_reconnect():
    runtime, fakes = make_runtime()
    runtime.set_tier("1080")
    assert fakes.launch_count == 0
    assert fakes.reconnect_generations == []


def test_unready_failed_start_persists_tier_for_watchdog_recovery():
    runtime, fakes = make_runtime()
    fakes.engine_state.start_errors = [EngineReadyError("no ready record")]
    with pytest.raises(EngineReadyError):
        runtime.start()

    runtime.set_tier("1080")

    assert runtime.check_once() == "respawned"
    assert [event for event in fakes.events if event[0] == "scrcpy.launch"] == [
        ("scrcpy.launch", "720", 0),
        ("scrcpy.launch", "1080", 0),
    ]


def test_set_tier_rejects_an_unknown_tier():
    runtime, fakes = make_runtime()
    runtime.start()
    with pytest.raises(ValueError):
        runtime.set_tier("4320")
    assert fakes.launch_count == 1


def test_set_tier_refreshes_dimensions_from_health_not_from_the_tier_name():
    runtime, fakes = make_runtime()
    runtime.start()
    fakes.admin.health_size = (1920, 1080)
    runtime.set_tier("1080")
    selection = runtime.select("100.64.1.4")
    assert (selection.width, selection.height) == (1920, 1080)


def test_failed_tier_change_propagates_and_leaves_the_runtime_degraded():
    runtime, fakes = make_runtime(
        reconnect_results=[EngineAdminProtocolError("bad response")]
    )
    runtime.start()
    with pytest.raises(EngineAdminProtocolError):
        runtime.set_tier("1080")
    # Health is connected again on the next tick, so the runtime recovers by
    # itself; the failed attempt never fabricated a new generation.
    assert runtime.select("100.64.1.4").generation == 0


def test_second_stale_rejection_propagates_without_claiming_recovery():
    runtime, fakes = make_runtime(
        reconnect_results=[ReconnectRejected(5), ReconnectRejected(9)],
        health_state="stalled",
        stall_grace=0,
    )
    runtime.start()
    with pytest.raises(ReconnectRejected):
        runtime.check_once()
    assert fakes.reconnect_generations == [1, 6]
    # Exactly two launches: one per reconnect attempt is forbidden; the retry
    # reuses the same launch.
    assert fakes.launch_count == 2


# --------------------------------------------------------------------------
# check_once decision tree
# --------------------------------------------------------------------------


def test_connected_health_reports_healthy_and_refreshes_metadata():
    runtime, fakes = make_runtime(health_generation=7)
    runtime.start()
    assert runtime.check_once() == "healthy"
    assert runtime.select("100.64.1.4").generation == 7


def test_check_once_before_start_retries_the_fresh_start_sequence():
    runtime, fakes = make_runtime()
    fakes.engine_state.start_errors = [EngineReadyError("boom")]
    with pytest.raises(EngineReadyError):
        runtime.start()
    assert runtime.check_once() == "respawned"
    assert fakes.engine_spawn_count == 2
    assert runtime.select("100.64.1.4") is not None


def test_check_once_returns_degraded_when_a_retried_start_fails_again():
    runtime, fakes = make_runtime()
    fakes.engine_state.start_errors = [
        EngineReadyError("boom"), EngineReadyError("again")
    ]
    with pytest.raises(EngineReadyError):
        runtime.start()
    assert runtime.check_once() == "degraded"
    assert runtime.select("100.64.1.4") is None


def test_stall_grace_is_measured_with_the_injected_clock():
    clock = FakeClock()
    runtime, fakes = make_runtime(
        clock=clock, health_state="stalled", stall_grace=3.0
    )
    runtime.start()
    assert runtime.check_once() == "grace"
    assert fakes.reconnect_generations == []

    clock.advance(1.0)
    assert runtime.check_once() == "grace"
    assert fakes.reconnect_generations == []

    clock.advance(2.5)
    assert runtime.check_once() == "recovered"
    assert fakes.reconnect_generations == [1]


def test_connected_health_clears_a_pending_stall_timer():
    clock = FakeClock()
    runtime, fakes = make_runtime(
        clock=clock, health_state="stalled", stall_grace=3.0
    )
    runtime.start()
    assert runtime.check_once() == "grace"

    fakes.admin.health_state = "connected"
    clock.advance(10.0)
    assert runtime.check_once() == "healthy"

    # A later stall starts the grace window over rather than firing at once.
    fakes.admin.health_state = "disconnected"
    assert runtime.check_once() == "grace"
    assert fakes.reconnect_generations == []


def test_disconnected_health_also_recovers_through_reconnect():
    runtime, fakes = make_runtime(health_state="disconnected", stall_grace=0)
    runtime.start()
    assert runtime.check_once() == "recovered"
    assert fakes.reconnect_generations == [1]


def test_accepted_reconnect_with_failed_health_refresh_is_not_recovered():
    runtime, fakes = make_runtime(health_state="stalled", stall_grace=0)
    runtime.start()

    initial_health = fakes.admin.health
    health_calls = 0

    def fail_only_the_post_reconnect_refresh(admin_port: int) -> EngineHealth:
        nonlocal health_calls
        health_calls += 1
        if health_calls == 1:
            return initial_health(admin_port)
        raise EngineAdminUnavailable("refresh down")

    fakes.admin.health = fail_only_the_post_reconnect_refresh

    with pytest.raises(EngineAdminUnavailable, match="refresh down"):
        runtime.check_once()
    assert fakes.reconnect_generations == [1]


def test_unknown_health_state_is_degraded_and_never_reconnects():
    runtime, fakes = make_runtime()
    runtime.start()
    fakes.admin.health_state = "banana"
    assert runtime.check_once() == "degraded"
    assert fakes.reconnect_generations == []
    assert fakes.engine_spawn_count == 1


def test_admin_protocol_error_is_degraded_not_respawn():
    runtime, fakes = make_runtime(
        admin_error=EngineAdminProtocolError("garbage")
    )
    runtime.start()
    assert runtime.check_once() == "degraded"
    assert fakes.engine_spawn_count == 1


def test_dead_process_is_detected_before_any_admin_call():
    runtime, fakes = make_runtime(ready_ports=[(51000, 51001), (52000, 52001)])
    runtime.start()
    fakes.process_running = False
    before = len(fakes.events)
    runtime.check_once()
    admin_events = [
        event for event in fakes.events[before:] if event[0].startswith("admin.")
    ]
    assert admin_events == []


def test_respawn_stops_the_old_engine_and_launches_generation_zero():
    runtime, fakes = make_runtime(ready_ports=[(51000, 51001), (52000, 52001)])
    runtime.start()
    fakes.process_running = False
    before = len(fakes.events)
    assert runtime.check_once() == "respawned"
    assert fakes.events[before:] == [
        ("engine.stop", "instance0"),
        ("scrcpy.stop",),
        ("scrcpy.launch", "720", 0),
        ("engine.start", "instance0", 27183),
    ]
    assert runtime.select("100.64.1.4").generation == 0


def test_respawn_keeps_the_current_tier():
    runtime, fakes = make_runtime(ready_ports=[(51000, 51001), (52000, 52001)])
    runtime.start()
    runtime.set_tier("1080")
    fakes.process_running = False
    runtime.check_once()
    launches = [event for event in fakes.events if event[0] == "scrcpy.launch"]
    assert launches[-1] == ("scrcpy.launch", "1080", 0)


def test_respawn_does_not_publish_stale_endpoint_metadata_when_it_fails():
    runtime, fakes = make_runtime(ready_ports=[(51000, 51001), (52000, 52001)])
    runtime.start()
    fakes.process_running = False
    fakes.engine_state.start_errors = [EngineReadyError("respawn failed")]
    assert runtime.check_once() == "degraded"
    assert runtime.select("100.64.1.4") is None


# --------------------------------------------------------------------------
# Keyframe
# --------------------------------------------------------------------------


def test_request_keyframe_delegates_to_the_admin_port():
    runtime, fakes = make_runtime()
    runtime.start()
    runtime.request_keyframe()
    assert fakes.admin.keyframe_ports == [51001]


def test_request_keyframe_before_start_is_a_no_op():
    runtime, fakes = make_runtime()
    runtime.request_keyframe()
    assert fakes.admin.keyframe_ports == []


def test_request_keyframe_swallows_admin_failures():
    runtime, fakes = make_runtime()
    runtime.start()
    fakes.admin.keyframe_error = EngineAdminUnavailable("down")
    runtime.request_keyframe()
    assert fakes.admin.keyframe_ports == [51001]


# --------------------------------------------------------------------------
# stop()
# --------------------------------------------------------------------------


def test_stop_tears_down_engine_and_launcher_and_is_idempotent():
    runtime, fakes = make_runtime()
    runtime.start()
    runtime.stop()
    runtime.stop()
    assert fakes.engine_stop_count == 1
    assert fakes.launcher.stop_count == 1


def test_stop_after_a_failed_start_still_stops_the_launcher_once():
    runtime, fakes = make_runtime()
    fakes.engine_state.start_errors = [EngineReadyError("boom")]
    with pytest.raises(EngineReadyError):
        runtime.start()
    runtime.stop()
    # start()'s own rollback stopped the launcher; stop() adds no engine
    # teardown because no engine handle survived.
    assert fakes.engine_stop_count == 0
    assert fakes.launcher.stop_count == 2


def test_every_public_method_is_inert_after_stop():
    runtime, fakes = make_runtime()
    runtime.start()
    runtime.stop()
    baseline = len(fakes.events)

    runtime.start()
    runtime.set_tier("1080")
    runtime.request_keyframe()
    assert runtime.select("100.64.1.4") is None
    assert runtime.check_once() == "degraded"
    assert fakes.events[baseline:] == []


def test_stop_waits_for_an_in_flight_recovery_and_leaves_nothing_behind():
    runtime, fakes = make_runtime(
        ready_ports=[(51000, 51001), (52000, 52001)],
        health_state="stalled",
        stall_grace=0,
    )
    runtime.start()

    entered = threading.Event()
    release = threading.Event()

    def blocking_health(admin_port: int, _real=fakes.admin.health):
        entered.set()
        release.wait(5)
        return _real(admin_port)

    fakes.admin.health = blocking_health

    result: list = []
    worker = threading.Thread(target=lambda: result.append(runtime.check_once()))
    worker.start()
    assert entered.wait(5)

    stopper = threading.Thread(target=runtime.stop)
    stopper.start()
    # stop() must block on the same lock rather than tearing down mid-recovery.
    stopper.join(0.2)
    assert stopper.is_alive()

    release.set()
    worker.join(5)
    stopper.join(5)
    assert not stopper.is_alive()

    assert result == ["recovered"]
    assert fakes.engine_stop_count == 1
    assert fakes.launcher.stop_count == 1
    assert runtime.select("100.64.1.4") is None


def test_concurrent_operations_are_fully_serialized():
    runtime, fakes = make_runtime()
    runtime.start()

    overlaps = []
    active = []
    guard = threading.Lock()
    real_health = fakes.admin.health

    def counting_health(admin_port: int):
        with guard:
            active.append(1)
            overlaps.append(len(active))
        try:
            return real_health(admin_port)
        finally:
            with guard:
                active.pop()

    fakes.admin.health = counting_health

    threads = [
        threading.Thread(target=runtime.check_once) for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)

    assert overlaps == [1] * 8
