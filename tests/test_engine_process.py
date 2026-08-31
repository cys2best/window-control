import os
import subprocess
import sys

import pytest

from server.engine_process import EngineInstance, EngineReadyError

FAKE_ENGINE = os.path.join(os.path.dirname(__file__), "fixtures", "fake_engine.py")


def make_fake_instance(mode: str = "ready", *,
                        ready_timeout_seconds: float = 1.0,
                        extra_env: dict[str, str] | None = None) -> EngineInstance:
    env = os.environ.copy()
    env["FAKE_ENGINE_MODE"] = mode
    env.update(extra_env or {})

    def fake_popen(args, **kwargs):
        return subprocess.Popen(
            [sys.executable, FAKE_ENGINE, *args[1:]], **kwargs
        )

    return EngineInstance(
        "test-instance", "engine.exe", 27183,
        env_overrides=env,
        popen=fake_popen,
        ready_timeout_seconds=ready_timeout_seconds,
    )


def make_capturing_instance(*, env_overrides: dict[str, str]) \
        -> tuple[EngineInstance, dict[str, object]]:
    captured: dict[str, object] = {}

    def capturing_popen(args, **kwargs):
        captured["env"] = kwargs["env"]
        return subprocess.Popen(
            [sys.executable, FAKE_ENGINE, *args[1:]], **kwargs
        )

    instance = EngineInstance(
        "test-instance", "engine.exe", 27183,
        env_overrides=env_overrides,
        popen=capturing_popen,
        ready_timeout_seconds=1.0,
    )
    return instance, captured


def test_spawn_inherits_parent_environment_and_overlays_engine_values(monkeypatch):
    monkeypatch.setenv("WINDOWCONTROL_PARENT_SENTINEL", "present")
    instance, captured = make_capturing_instance(
        env_overrides={"ENGINE_SIGNALING_TOKEN": "engine-jwt",
                        "FAKE_ENGINE_MODE": "ready"}
    )
    try:
        instance.start()
        assert captured["env"]["WINDOWCONTROL_PARENT_SENTINEL"] == "present"
        assert captured["env"]["ENGINE_SIGNALING_TOKEN"] == "engine-jwt"
    finally:
        instance.stop()


def test_ready_record_is_parsed_from_stdout():
    instance = make_fake_instance(mode="ready")
    try:
        record = instance.start()
        assert record.instance_name == "test-instance"
        assert record.whep_port == 8443
        assert record.admin_port == 8080
        assert record.generation == 0
        assert record.width == 1280
        assert record.height == 720
        assert record.pid > 0
        assert instance.is_running()
    finally:
        instance.stop()


def test_garbage_diagnostics_before_ready_record_are_skipped():
    instance = make_fake_instance(mode="noise_then_ready")
    try:
        record = instance.start()
        assert record.instance_name == "test-instance"
    finally:
        instance.stop()


@pytest.mark.parametrize("mode", ["wrong_instance", "invalid_port"])
def test_invalid_ready_record_is_rejected_and_process_is_stopped(mode):
    instance = make_fake_instance(mode=mode)
    with pytest.raises(EngineReadyError):
        instance.start()
    assert not instance.is_running()


def test_timeout_delay_is_passed_in_the_child_environment():
    instance = make_fake_instance(
        mode="slow",
        ready_timeout_seconds=0.05,
        extra_env={"FAKE_ENGINE_DELAY_SECONDS": "5"},
    )
    with pytest.raises(EngineReadyError, match="deadline"):
        instance.start()
    assert not instance.is_running()


def test_stderr_lines_reach_the_injected_log_callback():
    logged = []
    instance = make_fake_instance(mode="crash")
    instance._log = logged.append
    with pytest.raises(EngineReadyError):
        instance.start()
    assert any("fake engine crash" in line for line in logged)


def test_early_exit_before_ready_reports_exit_code():
    instance = make_fake_instance(mode="crash")
    with pytest.raises(EngineReadyError, match="exit code 3"):
        instance.start()
    assert not instance.is_running()


def test_stop_terminates_process_that_exits_promptly():
    instance = make_fake_instance(mode="ready")
    instance.start()
    assert instance.is_running()
    instance.stop()
    assert not instance.is_running()


def test_stop_kills_process_that_ignores_terminate(monkeypatch):
    instance = make_fake_instance(mode="ready")
    instance.start()
    assert instance.is_running()

    real_terminate = instance._process.terminate
    monkeypatch.setattr(instance._process, "terminate", lambda: None)

    try:
        instance.stop(timeout_seconds=0.2)
        assert not instance.is_running()
    finally:
        real_terminate()
        instance._process.wait(timeout=5)
