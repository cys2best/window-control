from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import httpx
import psutil
import pytest

import scripts.verify_python_orchestration as verifier

from scripts.verify_python_orchestration import (
    RealDeps,
    VerificationConfig,
    VerificationError,
    run_verification,
    submit_file_confirmation,
)


@dataclass
class FakeProcess:
    pid: int
    alive: bool = True
    exit_code: int = 0

    def poll(self):
        return None if self.alive else self.exit_code


class FakeDeps:
    def __init__(self, *, engines=None, skip_build=True, skip_tests=True, exit_on_tray=True,
                 quality_changes_pid=False, quality_stalls=False,
                 existing_app=False, discovery_failures=None,
                 exit_app_during_discovery=False, ready_devices=None,
                 ready_devices_after_tests=None, cleanup_prompt_error=None,
                 removal_stays_present=False, manual_removal_after_prompt=False):
        self.engines = list(engines or [])
        self.calls = []
        self.opened = []
        self.prompts = []
        self.prompt_records = []
        self.now = 1_000.0
        self.skip_build = skip_build
        self.skip_tests = skip_tests
        self.exit_on_tray = exit_on_tray
        self.quality_changes_pid = quality_changes_pid
        self.quality_stalls = quality_stalls
        self.discovery_failures = list(discovery_failures or [])
        self.exit_app_during_discovery = exit_app_during_discovery
        self.ready_devices = list(ready_devices or ["emulator-5556"])
        self.ready_devices_after_tests = ready_devices_after_tests
        self.cleanup_prompt_error = cleanup_prompt_error
        self.removal_stays_present = removal_stays_present
        self.manual_removal_after_prompt = manual_removal_after_prompt
        self.discover_calls = 0
        self.env = {}
        self.selection_count = 0
        self.generation = 0
        self.whep_port = 51000
        self.current_tier = "720"
        self.scrcpy_done = False
        self.engine_dead = False
        self.removed = False
        self.app = FakeProcess(300, alive=existing_app)
        self.relay = FakeProcess(301)
        self.page = FakeProcess(302)
        self.started_env = []
        self.child_envs = []
        self.progress = []

    def record_command(self, command, label):
        self.calls.append((label, tuple(command)))

    def run(self, command, *, cwd, env, label):
        self.calls.append((label, tuple(command)))
        self.child_envs.append(env)
        if label == "engine tests" and self.skip_tests:
            return
        if label == "engine tests" and self.ready_devices_after_tests is not None:
            self.ready_devices = list(self.ready_devices_after_tests)
        if label == "engine build" and self.skip_build:
            return

    def start(self, command, *, cwd, env, stdout_path, label):
        self.calls.append((label, tuple(command), stdout_path))
        self.child_envs.append(env)
        self.started_env.append(dict(env))
        if label == "local signaling relay":
            self.relay.alive = True
            return self.relay
        if label == "WindowControl app":
            self.app.alive = True
            self.engines = [FakeProcess(400)]
            return self.app
        if label == "verifier page server":
            return self.page
        raise AssertionError(label)

    def wait_for_tcp(self, host, ports, timeout):
        self.calls.append(("wait for tcp", host, tuple(ports), timeout))

    def stop_helper(self, process):
        self.calls.append(("stop helper", process.pid))
        process.alive = False

    def list_engine_processes(self):
        if self.removed:
            return []
        if self.engine_dead:
            return [FakeProcess(401)]
        if self.current_tier != "720" and self.quality_changes_pid:
            return [FakeProcess(499)]
        return self.engines

    def discover_vms(self):
        self.discover_calls += 1
        return [{"id": "adb:emulator-5556", "ldplayer_index": 7, "title": "vm"}]

    def adb_devices(self):
        return [] if self.removed else self.ready_devices

    def adb_forwards(self, serial, port=None):
        return [] if self.removed else ["emulator-5556 tcp:27190 localabstract:scrcpy_00000007"]

    def adb_state(self, serial):
        return "device"

    def kill_scrcpy(self, serial, scid):
        self.calls.append(("kill scrcpy", serial, scid))
        self.scrcpy_done = True

    def remove_device(self, serial, ldplayer_index=None):
        self.calls.append(("remove device", serial, ldplayer_index))
        if not self.removal_stays_present:
            self.removed = True

    def list_app_processes(self):
        return [] if not self.app.alive else [self.app]

    def kill_owned_engine(self, pid):
        self.calls.append(("kill engine", pid))
        self.engine_dead = True
        self.engines = []

    def api_instances(self):
        self.calls.append(("api instances", self.now))
        if self.discovery_failures:
            raise self.discovery_failures.pop(0)
        return [] if self.removed else [{"serial": "emulator-5556", "name": "instance1"}]

    def api_select(self, serial):
        self.selection_count += 1
        if self.scrcpy_done:
            self.generation = max(self.generation, 2)
        if self.engine_dead:
            self.generation = 0
            self.whep_port = 52000
        expiry = int(self.now) + 300
        return {
            "whep_url": f"http://192.0.2.10:{self.whep_port}/whep",
            "whep_token": f"{expiry}.instance1.token-{self.selection_count}",
            "generation": self.generation,
            "w": int(self.current_tier),
            "h": 1280,
            "signaling_url": None,
            "public_session": None,
        }

    def api_quality(self, serial, tier):
        self.calls.append(("quality", serial, tier))
        if tier != self.current_tier and not self.quality_stalls:
            self.current_tier = tier
            self.generation += 1
        return {"ok": True, "tier": tier}

    def open_browser(self, url):
        self.opened.append(url)

    def prompt(self, message, checkpoint=None):
        self.prompts.append(message)
        self.prompt_records.append((checkpoint, message))
        if self.manual_removal_after_prompt and "automatic removal checklist" in message.lower():
            self.removed = True
        if "tray exit" in message.lower() and self.exit_on_tray:
            self.app.alive = False
        return "PASS"

    def report_progress(self, message):
        self.progress.append(message)

    def cleanup_prompts(self):
        if self.cleanup_prompt_error is not None:
            raise self.cleanup_prompt_error

    def sleep(self, seconds):
        self.now += seconds
        if self.exit_app_during_discovery and self.discovery_failures:
            self.app.alive = False
            self.app.exit_code = 7

    def clock(self):
        return self.now


def config(**kwargs):
    values = dict(
        repo_root=Path("."),
        engine_exe=Path("engine/build/Release/engine.exe"),
        evidence_dir=Path("/tmp/window-control-verifier-test"),
        skip_expiry=True,
        keep_on_failure=False,
        enforce_windows=False,
        require_engine_binary=False,
    )
    values.update(kwargs)
    return VerificationConfig(**values)


def wait_until(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("timed out waiting for test file state")
        time.sleep(0.01)


def write_json_atomically(path, payload):
    temporary = path.with_name(path.name + ".test-tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, path)


def test_refuses_preexisting_engine_before_starting_any_owned_process():
    deps = FakeDeps(engines=[FakeProcess(99)])

    with pytest.raises(VerificationError, match="pre-existing engine"):
        run_verification(config(), deps)

    assert deps.calls == []
    assert deps.opened == []


def test_refuses_preexisting_windowcontrol_app_before_starting_or_running_commands():
    deps = FakeDeps(existing_app=True)

    with pytest.raises(VerificationError, match="pre-existing WindowControl app"):
        run_verification(config(), deps)

    assert deps.calls == []
    assert deps.opened == []


def test_refuses_multiple_adb_devices_even_when_a_serial_is_supplied():
    deps = FakeDeps(ready_devices=["emulator-5556", "device-123"])

    with pytest.raises(VerificationError, match="exactly one ADB device"):
        run_verification(
            config(
                serial="emulator-5556",
                skip_build=False,
                skip_tests=False,
                require_engine_binary=False,
            ),
            deps,
        )

    assert deps.calls == []
    assert deps.opened == []


def test_rechecks_the_same_single_adb_device_after_tests_before_discovery(tmp_path):
    repo_root = tmp_path / "repo"
    engine_dir = repo_root / "engine" / "build" / "Release"
    engine_dir.mkdir(parents=True)
    (engine_dir / "engine.exe").touch()
    (engine_dir / "engine_tests.exe").touch()
    deps = FakeDeps(
        skip_tests=False,
        ready_devices_after_tests=["emulator-5556", "device-123"],
    )

    with pytest.raises(VerificationError, match="exactly one ADB device"):
        run_verification(
            config(
                repo_root=repo_root,
                engine_exe=engine_dir / "engine.exe",
                evidence_dir=tmp_path / "evidence",
                skip_build=False,
                skip_tests=False,
            ),
            deps,
        )

    assert not any(call[0] == "WindowControl app" for call in deps.calls)
    assert not deps.opened
    assert deps.discover_calls == 0


def test_refuses_a_changed_sole_adb_device_after_tests_before_discovery(tmp_path):
    repo_root = tmp_path / "repo"
    engine_dir = repo_root / "engine" / "build" / "Release"
    engine_dir.mkdir(parents=True)
    (engine_dir / "engine.exe").touch()
    (engine_dir / "engine_tests.exe").touch()
    deps = FakeDeps(skip_tests=False, ready_devices_after_tests=["emulator-5558"])

    with pytest.raises(VerificationError, match="sole ready ADB device changed"):
        run_verification(
            config(
                repo_root=repo_root,
                engine_exe=engine_dir / "engine.exe",
                evidence_dir=tmp_path / "evidence",
                serial="emulator-5556",
                skip_build=False,
                skip_tests=False,
            ),
            deps,
        )

    assert not any(call[0] == "WindowControl app" for call in deps.calls)
    assert not deps.opened
    assert deps.discover_calls == 0


def test_real_preflight_refusal_creates_no_evidence_state(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    evidence_dir = tmp_path / "evidence"

    class ExistingApp:
        pid = 123
        info = {
            "pid": pid,
            "name": "python.exe",
            "cmdline": ["python.exe", str(repo_root / "src" / "main.py")],
            "cwd": str(repo_root),
        }

    monkeypatch.setattr("psutil.process_iter", lambda attrs: [ExistingApp()])
    verification_config = config(
        repo_root=repo_root,
        evidence_dir=evidence_dir,
    )

    with pytest.raises(VerificationError, match="pre-existing WindowControl app"):
        run_verification(verification_config, RealDeps(verification_config))

    assert not evidence_dir.exists()


def test_real_app_detection_matches_only_this_repo_python_script(monkeypatch, tmp_path):
    repo_root = tmp_path / "WindowControl"
    target = repo_root / "src" / "main.py"
    target.parent.mkdir(parents=True)
    target.touch()
    other_repo = tmp_path / "another-repo"

    class Process:
        def __init__(self, pid, cmdline, cwd, name="python.exe"):
            self.pid = pid
            self.info = {
                "pid": pid,
                "name": name,
                "cmdline": cmdline,
                "cwd": str(cwd),
            }

    processes = [
        Process(1, ["python.exe", "src/main.py"], repo_root),
        Process(2, ["python.exe", r".\src\..\src\main.py"], repo_root),
        Process(3, ["python.exe", str(target)], tmp_path),
        Process(4, ["PYTHON.EXE", str(target).upper()], tmp_path),
        Process(5, ["code.exe", "--search", str(target)], repo_root, "code.exe"),
        Process(6, ["python.exe", "-c", f"print({str(target)!r})"], repo_root),
        Process(7, ["python.exe", "src/main.py"], other_repo),
        Process(8, ["python.exe", "tools/check.py", "src/main.py"], repo_root),
    ]
    monkeypatch.setattr("psutil.process_iter", lambda attrs: processes)
    verification_config = config(repo_root=repo_root, evidence_dir=tmp_path / "evidence")

    found = RealDeps(verification_config).list_app_processes()

    assert {process.pid for process in found} == {1, 2, 3, 4}


def test_real_app_detection_skips_inaccessible_or_unresolvable_processes(
    monkeypatch, tmp_path
):
    repo_root = tmp_path / "WindowControl"

    class InaccessibleProcess:
        pid = 9

        @property
        def info(self):
            raise psutil.AccessDenied(self.pid)

    class Process:
        def __init__(self, pid, cwd):
            self.pid = pid
            self.info = {
                "pid": pid,
                "name": "python.exe",
                "cmdline": ["python.exe", "src/main.py"],
                "cwd": cwd,
            }

    processes = [
        InaccessibleProcess(),
        Process(10, None),
        Process(11, str(repo_root)),
    ]
    monkeypatch.setattr("psutil.process_iter", lambda attrs: processes)
    verification_config = config(repo_root=repo_root, evidence_dir=tmp_path / "evidence")

    found = RealDeps(verification_config).list_app_processes()

    assert {process.pid for process in found} == {11}


def test_every_owned_command_uses_one_sanitized_environment_without_mutating_parent(
    monkeypatch, tmp_path
):
    real_values = {
        "AUTH_TOKEN": "real-auth-must-not-be-forwarded",
        "PUBLIC_UI_URL": "https://real-ui.example",
        "TUNNEL_SECRET": "real-tunnel-secret",
    }
    for name, value in real_values.items():
        monkeypatch.setenv(name, value)
    engine_tests = tmp_path / "engine" / "build" / "Release" / "engine_tests.exe"
    engine_tests.parent.mkdir(parents=True)
    engine_tests.touch()
    deps = FakeDeps()

    run_verification(
        config(
            repo_root=tmp_path,
            evidence_dir=tmp_path / "evidence",
            skip_build=False,
            skip_tests=False,
        ),
        deps,
    )

    assert len(deps.child_envs) == 7
    assert len({id(env) for env in deps.child_envs}) == 1
    assert all(
        env[name] == ""
        for env in deps.child_envs
        for name in real_values
    )
    assert not any(
        secret in env.values()
        for env in deps.child_envs
        for secret in real_values.values()
    )
    assert {name: os.environ[name] for name in real_values} == real_values


def test_discovery_retries_transport_errors_while_owned_app_is_alive():
    request = httpx.Request("GET", "http://127.0.0.1:8080/instances")
    deps = FakeDeps(discovery_failures=[
        httpx.ConnectError("[WinError 10061] connection refused", request=request),
        httpx.ConnectError("[WinError 10061] connection refused", request=request),
    ])

    result = run_verification(config(), deps)

    assert result.checkpoints["discovery"]["status"] == "PASS"
    discovery_calls = [call for call in deps.calls if call[0] == "api instances"]
    assert discovery_calls[:3] == [
        ("api instances", 1_000.0),
        ("api instances", 1_001.0),
        ("api instances", 1_002.0),
    ]


def test_discovery_surfaces_http_401_without_retrying():
    request = httpx.Request("GET", "http://127.0.0.1:8080/instances")
    response = httpx.Response(401, request=request)
    deps = FakeDeps(discovery_failures=[
        httpx.HTTPStatusError("401 Unauthorized", request=request, response=response),
    ])

    result = run_verification(config(), deps)

    assert result.status == "FAIL"
    assert result.checkpoints["startup"]["detail"] == "401 Unauthorized"
    assert [call for call in deps.calls if call[0] == "api instances"] == [
        ("api instances", 1_000.0),
    ]


def test_discovery_fails_promptly_with_exit_code_when_owned_app_dies():
    request = httpx.Request("GET", "http://127.0.0.1:8080/instances")
    deps = FakeDeps(
        discovery_failures=[
            httpx.ConnectError("[WinError 10061] connection refused", request=request),
            httpx.ConnectError("must not be requested", request=request),
        ],
        exit_app_during_discovery=True,
    )

    result = run_verification(config(), deps)

    assert result.status == "FAIL"
    assert result.checkpoints["startup"]["detail"] == (
        "WindowControl app exited during discovery with exit code 7"
    )
    assert deps.now == 1_001.0
    assert [call for call in deps.calls if call[0] == "api instances"] == [
        ("api instances", 1_000.0),
    ]


def test_derives_scrcpy_port_and_scid_from_discovered_ldplayer_index():
    deps = FakeDeps()
    result = run_verification(config(), deps)

    assert result.status == "INCOMPLETE"
    assert ("kill scrcpy", "emulator-5556", 7) in deps.calls
    assert any(env.get("ENGINE_LOCAL_ICE_SERVERS") == "" for env in deps.started_env)
    assert any(env.get("ENGINE_PUBLIC_ICE_SERVERS") == "" for env in deps.started_env)


def test_runs_the_complete_unfiltered_engine_test_suite(tmp_path):
    repo_root = tmp_path / "repo"
    engine_dir = repo_root / "engine" / "build" / "Release"
    engine_dir.mkdir(parents=True)
    (engine_dir / "engine.exe").touch()
    (engine_dir / "engine_tests.exe").touch()
    deps = FakeDeps(skip_tests=False)

    run_verification(
        config(
            repo_root=repo_root,
            engine_exe=engine_dir / "engine.exe",
            skip_tests=False,
        ),
        deps,
    )

    engine_test_calls = [call for call in deps.calls if call[0] == "engine tests"]
    assert engine_test_calls
    for call in engine_test_calls:
        command = call[1]
        assert command == (str(engine_dir / "engine_tests.exe"),)
        assert not any(arg.startswith("--gtest_filter") for arg in command)
    call_labels = [call[0] for call in deps.calls]
    engine_tests_index = max(
        index for index, label in enumerate(call_labels) if label == "engine tests"
    )
    relay_index = call_labels.index("local signaling relay")
    readiness_index = call_labels.index("wait for tcp")
    assert relay_index < readiness_index < engine_tests_index
    assert deps.calls[readiness_index] == (
        "wait for tcp", "127.0.0.1", (8443, 8444), 30.0
    )
    assert ("stop helper", deps.relay.pid) in deps.calls[
        engine_tests_index + 1:
    ]


def test_starts_node_signaling_relay_with_cleared_jwt_secret():
    deps = FakeDeps()
    run_verification(config(), deps)

    relay_calls = [
        call for call in deps.calls if call[0] == "local signaling relay"
    ]
    assert len(relay_calls) == 1
    for relay_call in relay_calls:
        command = relay_call[1]
        assert command == ("node", "server.js")
        relay_index = deps.calls.index(relay_call)
        relay_env = deps.child_envs[relay_index]
        assert relay_env.get("JWT_SECRET") == ""
        assert relay_env.get("SIGNALING_TLS_PORT") == "8444"
        assert relay_env.get("SIGNALING_TLS_CERT_FILE") == str(
            Path("engine/test/tls/localhost-cert.pem")
        )
        assert relay_env.get("SIGNALING_TLS_KEY_FILE") == str(
            Path("engine/test/tls/localhost-key.pem")
        )
        assert relay_env.get("SSL_CERT_FILE") == str(Path("engine/test/tls/ca-cert.pem"))
        assert relay_env.get("ENGINE_TEST_WSS_PORT") == "8444"


def test_removal_passes_the_discovered_ldplayer_index_to_the_dependency():
    deps = FakeDeps()

    run_verification(config(), deps)

    assert ("remove device", "emulator-5556", 7) in deps.calls


def test_removal_timeout_enters_selected_device_manual_fallback_then_rechecks_absence():
    deps = FakeDeps(
        removal_stays_present=True,
        manual_removal_after_prompt=True,
    )

    result = run_verification(config(), deps)

    assert result.checkpoints["emulator removal"]["status"] == "PASS"
    assert any(
        checkpoint == "emulator removal" and "automatic removal checklist" in message.lower()
        for checkpoint, message in deps.prompt_records
    )


def test_real_emulator_removal_quits_only_the_selected_ldplayer_index(monkeypatch, tmp_path):
    commands = []
    console = r"C:\\LDPlayer\\LDPlayer9\\ldconsole.exe"

    monkeypatch.setattr("server.adb_manager._find_ldconsole", lambda: console, raising=False)
    monkeypatch.setattr("server.adb_manager.sys.platform", "win32")
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda command, **kwargs: commands.append((command, kwargs)) or SimpleNamespace(
            returncode=0, stdout="", stderr=""
        ),
    )
    deps = RealDeps(config(evidence_dir=tmp_path))
    tmp_path.mkdir(exist_ok=True)

    deps.remove_device("emulator-5558", 2)

    assert [command for command, _ in commands] == [
        [console, "quit", "--index", "2"],
    ]
    assert all("--all" not in command and "0" not in command for command, _ in commands)
    assert commands[0][1] == {
        "text": True,
        "capture_output": True,
        "check": False,
        "timeout": 15,
        "creationflags": 0x08000000,
    }


def test_real_non_emulator_removal_keeps_selected_adb_disconnect_behavior(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(
        verifier.subprocess,
        "run",
        lambda command, **kwargs: commands.append((command, kwargs)) or SimpleNamespace(
            returncode=0, stdout="", stderr=""
        ),
    )
    deps = RealDeps(config(evidence_dir=tmp_path))
    tmp_path.mkdir(exist_ok=True)

    deps.remove_device("device-serial", 7)

    assert [command for command, _ in commands] == [["adb", "disconnect", "device-serial"]]


@pytest.mark.parametrize(
    ("console", "outcome", "expected"),
    [
        (None, SimpleNamespace(returncode=0, stdout="", stderr=""), "ldconsole.exe is unavailable"),
        (r"C:\\LDPlayer\\LDPlayer9\\ldconsole.exe", SimpleNamespace(returncode=9, stdout="", stderr="not ready"), "exit code 9"),
        (r"C:\\LDPlayer\\LDPlayer9\\ldconsole.exe", subprocess.TimeoutExpired("ldconsole.exe", 15, stderr="timed out"), "timed out"),
        (r"C:\\LDPlayer\\LDPlayer9\\ldconsole.exe", PermissionError("access denied secret=do-not-log"), "secret=<redacted>"),
    ],
)
def test_real_emulator_removal_reports_unavailable_or_unsuccessful_ldconsole(
    monkeypatch, tmp_path, console, outcome, expected
):
    monkeypatch.setattr("server.adb_manager._find_ldconsole", lambda: console, raising=False)

    def run(*_args, **_kwargs):
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    monkeypatch.setattr(verifier.subprocess, "run", run)
    deps = RealDeps(config(evidence_dir=tmp_path))
    tmp_path.mkdir(exist_ok=True)

    with pytest.raises(VerificationError, match=expected):
        deps.remove_device("emulator-5554", 0)


def test_expiry_wait_mints_unequal_token_and_opens_independent_second_page():
    deps = FakeDeps()
    result = run_verification(config(skip_expiry=False), deps)

    assert result.checkpoints["expiry"]["status"] == "PASS"
    assert len(deps.opened) >= 2
    assert result.status == "INCOMPLETE"


def test_explicit_expiry_skip_is_marked_incomplete():
    deps = FakeDeps()
    result = run_verification(config(skip_expiry=True), deps)

    assert result.checkpoints["expiry"]["status"] == "SKIP"
    assert result.status == "INCOMPLETE"


def test_quality_and_recovery_are_commands_with_polling_not_prompt_only():
    deps = FakeDeps()
    run_verification(config(), deps)

    labels = [call[0] for call in deps.calls]
    assert "quality" in labels
    assert "kill scrcpy" in labels
    assert "kill engine" in labels
    assert "remove device" in labels
    assert any("dimension" in prompt.lower() for prompt in deps.prompts)


@pytest.mark.parametrize(
    ("configured_tier", "expected_target"),
    [
        ("480", "720"),
        ("720", "480"),
        ("1080", "480"),
        ("1440", "480"),
    ],
)
def test_configured_quality_baseline_then_transition_uses_distinct_supported_tier(
    configured_tier, expected_target
):
    deps = FakeDeps()

    result = run_verification(config(tier=configured_tier), deps)

    assert [call for call in deps.calls if call[0] == "quality"] == [
        ("quality", "emulator-5556", configured_tier),
        ("quality", "emulator-5556", expected_target),
    ]
    assert result.summary["initial_selection"]["w"] == int(configured_tier)
    assert result.summary["quality"]["w"] == int(expected_target)
    assert (
        result.summary["quality"]["generation"]
        > result.summary["initial_selection"]["generation"]
    )


def test_manual_gate_prompts_expose_complete_operator_checklists():
    deps = FakeDeps()

    run_verification(config(skip_expiry=False), deps)

    prompts = dict(deps.prompt_records)
    expected_keywords = {
        "first peer": ("non-black", "changing", "framesdecoded", "datachannel", "click"),
        "second peer": ("fresh", "independently", "framesdecoded", "original"),
        "quality": ("existing", "dimensions", "framesdecoded", "without reloading"),
        "scrcpy recovery": ("existing", "resume", "framesdecoded", "without reloading"),
        "engine respawn": ("fresh", "framesdecoded", "datachannel", "click"),
        "emulator removal": ("selected", "instance", "engine", "forward"),
        "application exit": ("tray", "app", "engine", "selected", "forward"),
    }

    assert set(expected_keywords) <= set(prompts)
    for checkpoint, keywords in expected_keywords.items():
        prompt = prompts[checkpoint].lower()
        assert "checklist" in prompt
        assert all(keyword in prompt for keyword in keywords)


def test_automatic_removal_fallback_limits_manual_action_to_selected_device():
    class RemovalFailureDeps(FakeDeps):
        def remove_device(self, serial, ldplayer_index):
            raise VerificationError("selected removal command failed")

    deps = RemovalFailureDeps()

    run_verification(config(), deps)

    fallback = next(
        message for checkpoint, message in deps.prompt_records
        if checkpoint == "emulator removal" and "automatic removal" in message.lower()
    )
    assert "only the selected device" in fallback.lower()
    assert "emulator-5556" in fallback
    assert "other devices" in fallback.lower()


def test_quality_rejects_engine_pid_replacement_before_visual_prompt():
    deps = FakeDeps(quality_changes_pid=True)
    result = run_verification(config(), deps)

    assert result.status == "FAIL"
    assert result.checkpoints["quality"]["status"] == "FAIL"


def test_source_loss_is_triggered_immediately_before_emulator_removal():
    deps = FakeDeps()
    run_verification(config(), deps)

    labels = [call[0] for call in deps.calls]
    assert labels.index("kill scrcpy", labels.index("kill engine")) < labels.index("remove device")


def test_timeout_failure_preserves_partial_result_and_stops_only_helpers():
    deps = FakeDeps(quality_stalls=True)
    result = run_verification(config(), deps)

    assert result.status == "FAIL"
    assert result.checkpoints["quality"]["status"] == "FAIL"
    assert result.summary["failed_gate"] == "quality"
    assert ("stop helper", 301) in deps.calls
    assert ("stop helper", 302) in deps.calls
    assert not any(call[0] == "kill app" for call in deps.calls)


def test_prompt_cleanup_failure_preserves_result_and_stops_helpers():
    deps = FakeDeps(
        quality_stalls=True,
        cleanup_prompt_error=OSError("prompt response is temporarily locked"),
    )

    result = run_verification(config(), deps)

    assert result.status == "FAIL"
    assert result.checkpoints["quality"]["status"] == "FAIL"
    assert ("stop helper", 301) in deps.calls
    assert ("stop helper", 302) in deps.calls
    assert any(
        "prompt cleanup unavailable" in message.lower()
        for message in deps.progress
    )


def test_keep_on_failure_retains_app_and_engine_but_stops_helpers():
    deps = FakeDeps(quality_stalls=True)
    result = run_verification(config(keep_on_failure=True), deps)

    assert result.status == "FAIL"
    assert ("stop helper", 301) in deps.calls
    assert ("stop helper", 302) in deps.calls
    retained = result.summary["retained_on_failure"]
    assert retained == {
        "keep_on_failure": True,
        "app": True,
        "owned_engine_pids": [400],
        "selected_forward": True,
    }


def test_app_exit_requires_operator_exit_and_never_force_kills_app():
    deps = FakeDeps(exit_on_tray=False)
    result = run_verification(config(), deps)

    assert result.checkpoints["application exit"]["status"] == "FAIL"
    assert result.status == "FAIL"
    assert not any(call[0] == "kill app" for call in deps.calls)


def test_missing_engine_tests_is_a_failure_by_default(tmp_path):
    deps = FakeDeps(skip_tests=False)

    with pytest.raises(VerificationError, match="engine_tests.exe"):
        run_verification(config(repo_root=tmp_path, evidence_dir=tmp_path, skip_tests=False, require_engine_binary=False), deps)


def test_fragment_browser_url_contains_no_query_api_or_token_log_payload(tmp_path):
    deps = FakeDeps()
    run_verification(config(evidence_dir=tmp_path), deps)

    assert deps.opened
    url = deps.opened[0]
    assert "?" not in url
    assert "#" in url
    assert "engine-select" not in url
    assert "Authorization" not in url


def test_engine_ice_environment_is_cleared_then_restored(monkeypatch):
    monkeypatch.setenv("ENGINE_LOCAL_ICE_SERVERS", "stale-local")
    monkeypatch.setenv("ENGINE_PUBLIC_ICE_SERVERS", "stale-public")
    deps = FakeDeps()

    run_verification(config(), deps)

    assert os.environ["ENGINE_LOCAL_ICE_SERVERS"] == "stale-local"
    assert os.environ["ENGINE_PUBLIC_ICE_SERVERS"] == "stale-public"


def test_real_adb_forward_listing_is_global_and_exactly_filtered(monkeypatch, tmp_path):
    deps = RealDeps(config(evidence_dir=tmp_path))
    calls = []

    class Completed:
        returncode = 0
        stdout = "emulator-5556 tcp:27190 localabstract:scrcpy_00000007\nother tcp:27190 localabstract:other\nselected tcp:2719 localabstract:near\n"

    def fake_adb(args):
        calls.append(args)
        return Completed()

    monkeypatch.setattr(deps, "adb", fake_adb)
    assert deps.adb_forwards("emulator-5556", 27190) == [
        "emulator-5556 tcp:27190 localabstract:scrcpy_00000007"
    ]
    assert calls == [["forward", "--list"]]


def test_real_verifier_recovery_kill_targets_only_the_selected_android_server(monkeypatch, tmp_path):
    """Catches verifier recovery killing no Server process or a different scid."""
    verification_config = config(repo_root=tmp_path, evidence_dir=tmp_path / "evidence")
    deps = RealDeps(verification_config)
    adb_calls = []

    def adb(args):
        adb_calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(deps, "adb", adb)

    deps.kill_scrcpy("emulator-5554", 0)

    expected = [
        "-s",
        "emulator-5554",
        "shell",
        "pkill -f 'com[.]genymobile[.]scrcpy[.]Server.*scid=0$'",
    ]
    assert adb_calls == [expected]

    pattern = expected[-1].removeprefix("pkill -f '").removesuffix("'")
    assert re.search(pattern, "app_process / com.genymobile.scrcpy.Server 3.1 scid=0")
    assert not re.search(pattern, "app_process / com.genymobile.scrcpy.CleanUp 3.1 scid=0")
    assert not re.search(pattern, "app_process / com.genymobile.scrcpy.Server 3.1 scid=1")


@pytest.mark.parametrize(
    ("returncode", "stderr", "expected_detail"),
    [
        (1, "pkill: no process matched\n", "pkill: no process matched"),
        (7, "adb: transport offline\n", "adb: transport offline"),
    ],
)
def test_real_verifier_recovery_kill_surfaces_adb_failure(
    monkeypatch, tmp_path, returncode, stderr, expected_detail
):
    """Catches recovery waiting for a watchdog after selected-source kill failed."""
    deps = RealDeps(config(repo_root=tmp_path, evidence_dir=tmp_path / "evidence"))
    monkeypatch.setattr(
        deps,
        "adb",
        lambda args: SimpleNamespace(returncode=returncode, stdout="", stderr=stderr),
    )

    with pytest.raises(VerificationError) as raised:
        deps.kill_scrcpy("emulator-5554", 0)

    assert f"exit code {returncode}" in str(raised.value)
    assert expected_detail in str(raised.value)


def test_real_verifier_recovery_kill_redacts_and_bounds_adb_stderr(monkeypatch, tmp_path):
    """Catches recovery errors leaking an uncontrolled ADB stderr blob."""
    deps = RealDeps(config(repo_root=tmp_path, evidence_dir=tmp_path / "evidence"))
    stderr = "AUTH_TOKEN=must-not-leak\n" + ("diagnostic " * 100)
    monkeypatch.setattr(
        deps,
        "adb",
        lambda args: SimpleNamespace(returncode=7, stdout="", stderr=stderr),
    )

    with pytest.raises(VerificationError) as raised:
        deps.kill_scrcpy("emulator-5554", 0)

    message = str(raised.value)
    assert "AUTH_TOKEN=<redacted>" in message
    assert "must-not-leak" not in message
    assert len(message) < 400


def test_standalone_runner_can_import_src_without_pytest_pythonpath(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    evidence_dir = tmp_path / "standalone-evidence"
    script = f"""
from pathlib import Path
import sys
from types import SimpleNamespace
from scripts.verify_python_orchestration import RealDeps, VerificationConfig

sourceRoot = str((Path.cwd() / "src").resolve())
sys.path[:] = [path for path in sys.path if str(Path(path or ".").resolve()) != sourceRoot]
assert sourceRoot not in [str(Path(path or ".").resolve()) for path in sys.path]

config = VerificationConfig(
    repo_root=Path.cwd(),
    engine_exe=Path.cwd() / "engine.exe",
    evidence_dir=Path({str(evidence_dir)!r}),
    enforce_windows=False,
)
deps = RealDeps(config)
deps.discover_vms()
adb_calls = []
deps.adb = lambda args: adb_calls.append(args) or SimpleNamespace(
    returncode=0, stdout="", stderr=""
)
deps.kill_scrcpy("emulator-5554", 0)
assert adb_calls == [[
    "-s", "emulator-5554", "shell",
    "pkill -f 'com[.]genymobile[.]scrcpy[.]Server.*scid=0$'",
]]
print("standalone discovery and recovery kill imported src")
"""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=environment,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "standalone discovery and recovery kill imported src" in completed.stdout


def test_file_prompt_mode_uses_unique_nonce_and_consumes_responses_once(
    monkeypatch, tmp_path, capsys
):
    repo_root = tmp_path / "repo"
    evidence_dir = repo_root / "engine" / "test" / "verification-one"
    evidence_dir.mkdir(parents=True)
    monkeypatch.setenv("AUTH_TOKEN", "real-auth-secret-must-not-appear")
    monkeypatch.setattr(
        "builtins.input",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("file prompt mode must not read stdin")
        ),
    )
    monkeypatch.setattr("psutil.pid_exists", lambda pid: pid == os.getpid())
    verification_config = config(
        repo_root=repo_root,
        evidence_dir=evidence_dir,
        file_prompts=True,
        file_prompt_poll_seconds=0.01,
    )
    deps = RealDeps(verification_config)
    prompt_path = evidence_dir / "active-prompt.json"
    answers = {}
    first_message = (
        "First peer checklist:\n"
        "- Confirm non-black, changing live video.\n"
        "- Confirm framesDecoded increases and DataChannel is open.\n"
        "- Click the video and confirm the device reacts."
    )

    def ask(key, checkpoint, message):
        try:
            answers[key] = deps.prompt(message, checkpoint=checkpoint)
        except BaseException as error:
            answers[key] = error

    first = threading.Thread(
        target=ask,
        args=("first", "first peer", first_message),
        daemon=True,
    )
    first.start()
    wait_until(prompt_path.exists)
    console = capsys.readouterr().out
    assert r".\engine\verify-python-orchestration.ps1 -Confirm PASS" in console
    assert first_message in console
    first_prompt = json.loads(prompt_path.read_text(encoding="utf-8"))
    assert first_prompt == {
        "version": 1,
        "verifier_pid": os.getpid(),
        "verifier_started_at": first_prompt["verifier_started_at"],
        "nonce": first_prompt["nonce"],
        "checkpoint": "first peer",
        "message": first_message,
        "expected_results": ["PASS", "FAIL"],
    }
    assert first_prompt["nonce"]
    assert "real-auth-secret-must-not-appear" not in json.dumps(first_prompt)
    response_path = verifier.FilePromptChannel.response_path(
        evidence_dir, first_prompt["nonce"]
    )

    submit_file_confirmation(repo_root, "PASS")
    first.join(timeout=2)
    assert not first.is_alive()
    assert answers["first"] == "PASS"
    assert not prompt_path.exists()
    assert not response_path.exists()

    second = threading.Thread(
        target=ask,
        args=("second", "second peer", "Confirm second peer video"),
        daemon=True,
    )
    second.start()
    wait_until(prompt_path.exists)
    second_prompt = json.loads(prompt_path.read_text(encoding="utf-8"))
    assert second_prompt["nonce"] != first_prompt["nonce"]
    response_path = verifier.FilePromptChannel.response_path(
        evidence_dir, second_prompt["nonce"]
    )

    write_json_atomically(
        response_path,
        {
            "version": 1,
            "verifier_pid": os.getpid(),
            "verifier_started_at": second_prompt["verifier_started_at"] + 1,
            "nonce": second_prompt["nonce"],
            "result": "PASS",
        },
    )
    wait_until(lambda: not response_path.exists())
    assert second.is_alive()

    submit_file_confirmation(repo_root, "FAIL")
    second.join(timeout=2)
    assert not second.is_alive()
    assert answers["second"] == "FAIL"
    assert not prompt_path.exists()
    assert not response_path.exists()


def test_confirmation_ignores_dead_run_and_targets_only_live_prompt(
    monkeypatch, tmp_path
):
    repo_root = tmp_path / "repo"
    test_root = repo_root / "engine" / "test"
    dead_dir = test_root / "verification-dead"
    live_dir = test_root / "verification-live"
    dead_dir.mkdir(parents=True)
    live_dir.mkdir(parents=True)
    prompt = {
        "version": 1,
        "verifier_pid": 101,
        "verifier_started_at": 10.0,
        "nonce": "dead-nonce",
        "checkpoint": "first peer",
        "message": "Confirm video",
        "expected_results": ["PASS", "FAIL"],
    }
    (dead_dir / "active-prompt.json").write_text(
        json.dumps(prompt), encoding="utf-8"
    )
    live_prompt = dict(prompt, verifier_pid=202, nonce="live-nonce")
    (live_dir / "active-prompt.json").write_text(
        json.dumps(live_prompt), encoding="utf-8"
    )
    monkeypatch.setattr("psutil.pid_exists", lambda pid: pid == 202)
    monkeypatch.setattr(
        verifier,
        "_pid_started_at",
        lambda pid: 10.0 if pid == 202 else None,
    )

    submit_file_confirmation(repo_root, "PASS")

    assert not verifier.FilePromptChannel.response_path(
        dead_dir, "dead-nonce"
    ).exists()
    assert json.loads(
        verifier.FilePromptChannel.response_path(
            live_dir, "live-nonce"
        ).read_text(encoding="utf-8")
    ) == {
        "version": 1,
        "verifier_pid": 202,
        "verifier_started_at": 10.0,
        "nonce": "live-nonce",
        "result": "PASS",
    }


def test_confirmation_rejects_a_reused_verifier_pid(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    evidence_dir = repo_root / "engine" / "test" / "verification-reused-pid"
    evidence_dir.mkdir(parents=True)
    (evidence_dir / "active-prompt.json").write_text(
        json.dumps(
            {
                "version": 1,
                "verifier_pid": 404,
                "verifier_started_at": 100.0,
                "nonce": "old-nonce",
                "checkpoint": "first peer",
                "message": "Confirm video",
                "expected_results": ["PASS", "FAIL"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr("psutil.pid_exists", lambda pid: pid == 404)
    monkeypatch.setattr(
        verifier,
        "_pid_started_at",
        lambda pid: 200.0 if pid == 404 else None,
        raising=False,
    )

    with pytest.raises(VerificationError, match="no live active file prompt"):
        submit_file_confirmation(repo_root, "PASS")

    assert not verifier.FilePromptChannel.response_path(
        evidence_dir, "old-nonce"
    ).exists()


def test_confirmation_does_not_overwrite_a_pending_response(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    evidence_dir = repo_root / "engine" / "test" / "verification-pending"
    evidence_dir.mkdir(parents=True)
    prompt = {
        "version": 1,
        "verifier_pid": 404,
        "verifier_started_at": 100.0,
        "nonce": "active-nonce",
        "checkpoint": "first peer",
        "message": "Confirm video",
        "expected_results": ["PASS", "FAIL"],
    }
    (evidence_dir / "active-prompt.json").write_text(
        json.dumps(prompt), encoding="utf-8"
    )
    response_path = verifier.FilePromptChannel.response_path(
        evidence_dir, "active-nonce"
    )
    response_path.write_text(
        json.dumps({**prompt, "result": "PASS"}), encoding="utf-8"
    )
    monkeypatch.setattr(verifier, "_pid_started_at", lambda pid: 100.0)

    with pytest.raises(VerificationError, match="confirmation already submitted"):
        submit_file_confirmation(repo_root, "FAIL")

    assert json.loads(response_path.read_text(encoding="utf-8"))["result"] == "PASS"


def test_confirmation_race_cannot_satisfy_a_replacement_prompt(monkeypatch, tmp_path):
    repo_root = tmp_path / "repo"
    evidence_dir = repo_root / "engine" / "test" / "verification-race"
    evidence_dir.mkdir(parents=True)
    prompt_path = evidence_dir / "active-prompt.json"
    original_prompt = {
        "version": 1,
        "verifier_pid": 404,
        "verifier_started_at": 100.0,
        "nonce": "old-nonce",
        "checkpoint": "first peer",
        "message": "Confirm first peer",
        "expected_results": ["PASS", "FAIL"],
    }
    replacement_prompt = {
        **original_prompt,
        "nonce": "next-nonce",
        "checkpoint": "quality",
        "message": "Confirm quality",
    }
    prompt_path.write_text(json.dumps(original_prompt), encoding="utf-8")
    monkeypatch.setattr(verifier, "_pid_started_at", lambda pid: 100.0)
    write_once = verifier._write_json_once

    def replace_prompt_then_publish(path, payload):
        write_json_atomically(prompt_path, replacement_prompt)
        write_once(path, payload)

    monkeypatch.setattr(verifier, "_write_json_once", replace_prompt_then_publish)

    response_path = submit_file_confirmation(repo_root, "PASS")

    assert response_path == verifier.FilePromptChannel.response_path(
        evidence_dir, "old-nonce"
    )
    assert response_path.exists()
    assert not verifier.FilePromptChannel.response_path(
        evidence_dir, "next-nonce"
    ).exists()
    assert json.loads(prompt_path.read_text(encoding="utf-8"))["nonce"] == "next-nonce"


def test_confirmation_refuses_zero_or_ambiguous_live_prompts(
    monkeypatch, tmp_path
):
    repo_root = tmp_path / "repo"
    test_root = repo_root / "engine" / "test"
    test_root.mkdir(parents=True)
    monkeypatch.setattr("psutil.pid_exists", lambda pid: True)
    monkeypatch.setattr(verifier, "_pid_started_at", lambda pid: 10.0)

    with pytest.raises(VerificationError, match="no live active file prompt"):
        submit_file_confirmation(repo_root, "PASS")

    for index in (1, 2):
        evidence_dir = test_root / f"verification-{index}"
        evidence_dir.mkdir()
        (evidence_dir / "active-prompt.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "verifier_pid": 300 + index,
                    "verifier_started_at": 10.0,
                    "nonce": f"nonce-{index}",
                    "checkpoint": "first peer",
                    "message": "Confirm video",
                    "expected_results": ["PASS", "FAIL"],
                }
            ),
            encoding="utf-8",
        )

    with pytest.raises(VerificationError, match="multiple live active file prompts"):
        submit_file_confirmation(repo_root, "FAIL")
    assert not list(test_root.glob("verification-*/prompt-response-*.json"))


def test_expiry_wait_reports_bounded_remaining_time_without_token():
    deps = FakeDeps()

    run_verification(config(skip_expiry=False), deps)

    assert deps.progress[0] == "WHEP token expiry wait: 301 seconds remaining"
    assert deps.progress[-1] == "WHEP token expiry wait: complete"
    remaining = [
        int(message.split(": ", 1)[1].split(" ", 1)[0])
        for message in deps.progress[:-1]
    ]
    assert all(0 < before - after <= 30 for before, after in zip(remaining, remaining[1:]))
    assert "token-" not in "\n".join(deps.progress)


def test_real_expiry_progress_is_visible_and_written_to_verification_log(
    tmp_path, capsys
):
    deps = RealDeps(config(evidence_dir=tmp_path))
    tmp_path.mkdir(exist_ok=True)

    deps.report_progress("WHEP token expiry wait: 42 seconds remaining")

    assert capsys.readouterr().out == "WHEP token expiry wait: 42 seconds remaining\n"
    assert (tmp_path / "verification.log").read_text(encoding="utf-8").endswith(
        " WHEP token expiry wait: 42 seconds remaining\n"
    )
