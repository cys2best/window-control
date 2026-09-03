import os
import queue
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


def test_spawn_suppresses_console_window_on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    captured: dict[str, object] = {}

    def capturing_popen(args, **kwargs):
        captured["kwargs"] = kwargs
        kwargs = dict(kwargs)
        kwargs.pop("creationflags", None)
        return subprocess.Popen([sys.executable, FAKE_ENGINE, *args[1:]], **kwargs)

    env = os.environ.copy()
    env["FAKE_ENGINE_MODE"] = "ready"
    instance = EngineInstance(
        "test-instance", "engine.exe", 27183,
        env_overrides=env,
        popen=capturing_popen,
        ready_timeout_seconds=1.0,
    )
    try:
        instance.start()
        assert captured["kwargs"]["creationflags"] == 0x08000000
    finally:
        instance.stop()


def test_spawn_omits_creationflags_off_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    captured: dict[str, object] = {}

    def capturing_popen(args, **kwargs):
        captured["kwargs"] = kwargs
        return subprocess.Popen([sys.executable, FAKE_ENGINE, *args[1:]], **kwargs)

    env = os.environ.copy()
    env["FAKE_ENGINE_MODE"] = "ready"
    instance = EngineInstance(
        "test-instance", "engine.exe", 27183,
        env_overrides=env,
        popen=capturing_popen,
        ready_timeout_seconds=1.0,
    )
    try:
        instance.start()
        assert "creationflags" not in captured["kwargs"]
    finally:
        instance.stop()


def test_spawn_excludes_auth_token_and_preserves_engine_environment(monkeypatch):
    monkeypatch.setenv("WINDOWCONTROL_PARENT_SENTINEL", "present")
    monkeypatch.setenv("AUTH_TOKEN", "raw-native-control-secret")
    instance, captured = make_capturing_instance(
        env_overrides={"ENGINE_SIGNALING_TOKEN": "engine-jwt",
                        "FAKE_ENGINE_MODE": "ready"}
    )
    try:
        instance.start()
        assert captured["env"]["WINDOWCONTROL_PARENT_SENTINEL"] == "present"
        assert captured["env"]["ENGINE_SIGNALING_TOKEN"] == "engine-jwt"
        assert "AUTH_TOKEN" not in captured["env"]
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


@pytest.mark.parametrize("mode", ["wrong_instance", "invalid_port", "bool_field"])
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


def test_stdout_eof_waits_for_exit_code_publication_before_reporting():
    class ExitCodePublishedByWait:
        returncode = None

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            assert 0 < timeout <= 1.0
            self.returncode = 3
            return self.returncode

    instance = EngineInstance(
        "test-instance", "engine.exe", 27183,
        env_overrides={},
        ready_timeout_seconds=1.0,
        clock=lambda: 10.0,
    )
    instance._process = ExitCodePublishedByWait()
    stdout_queue = queue.Queue()
    stdout_queue.put(None)

    with pytest.raises(EngineReadyError, match="exit code 3"):
        instance._await_ready_record(stdout_queue, deadline=11.0)


def test_stop_terminates_process_that_exits_promptly():
    instance = make_fake_instance(mode="ready")
    instance.start()
    assert instance.is_running()
    instance.stop()
    assert not instance.is_running()


def test_stop_kills_and_reaps_process_after_terminate_timeout():
    class ProcessThatIgnoresTerminate:
        returncode = None
        killed = False

        def poll(self):
            return self.returncode

        def terminate(self):
            pass

        def wait(self, timeout):
            if not self.killed:
                raise subprocess.TimeoutExpired("engine.exe", timeout)
            self.returncode = -9
            return self.returncode

        def kill(self):
            self.killed = True

    instance = EngineInstance(
        "test-instance", "engine.exe", 27183,
        env_overrides={},
    )
    instance._process = ProcessThatIgnoresTerminate()

    instance.stop(timeout_seconds=0.2)

    assert not instance.is_running()


def test_stop_kills_and_reaps_real_process_when_terminate_is_ignored(monkeypatch):
    instance = make_fake_instance(mode="ready")
    instance.start()
    process = instance._process
    monkeypatch.setattr(process, "terminate", lambda: None)

    try:
        instance.stop(timeout_seconds=5.0)
        assert not instance.is_running()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5.0)
