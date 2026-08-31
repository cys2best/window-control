from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

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
                 exit_app_during_discovery=False, ready_devices=None):
        self.engines = list(engines or [])
        self.calls = []
        self.opened = []
        self.prompts = []
        self.now = 1_000.0
        self.skip_build = skip_build
        self.skip_tests = skip_tests
        self.exit_on_tray = exit_on_tray
        self.quality_changes_pid = quality_changes_pid
        self.quality_stalls = quality_stalls
        self.discovery_failures = list(discovery_failures or [])
        self.exit_app_during_discovery = exit_app_during_discovery
        self.ready_devices = list(ready_devices or ["emulator-5556"])
        self.env = {}
        self.selection_count = 0
        self.generation = 0
        self.whep_port = 51000
        self.quality_done = False
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
        if label == "engine build" and self.skip_build:
            return

    def start(self, command, *, cwd, env, stdout_path, label):
        self.calls.append((label, tuple(command), stdout_path))
        self.child_envs.append(env)
        self.started_env.append(dict(env))
        if label == "local signaling relay":
            return self.relay
        if label == "WindowControl app":
            self.app.alive = True
            self.engines = [FakeProcess(400)]
            return self.app
        if label == "verifier page server":
            return self.page
        raise AssertionError(label)

    def stop_helper(self, process):
        self.calls.append(("stop helper", process.pid))
        process.alive = False

    def list_engine_processes(self):
        if self.removed:
            return []
        if self.engine_dead:
            return [FakeProcess(401)]
        if self.quality_done and self.quality_changes_pid:
            return [FakeProcess(499)]
        return self.engines

    def discover_vms(self):
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

    def remove_device(self, serial):
        self.calls.append(("remove device", serial))
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
        if self.quality_done:
            self.generation = max(self.generation, 1)
            if self.quality_stalls:
                self.generation = 0
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
            "w": 720 if not self.quality_done else 1080,
            "h": 1280,
            "signaling_url": None,
            "signaling_token": None,
        }

    def api_quality(self, serial, tier):
        self.calls.append(("quality", serial, tier))
        self.quality_done = True
        return {"ok": True, "tier": tier}

    def open_browser(self, url):
        self.opened.append(url)

    def prompt(self, message, checkpoint=None):
        self.prompts.append(message)
        if "WindowControl tray Exit" in message and self.exit_on_tray:
            self.app.alive = False
        return "PASS"

    def report_progress(self, message):
        self.progress.append(message)

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

    assert len(deps.child_envs) == 6
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
    assert any(env.get("ENGINE_SIGNALING_SECRET") == "" for env in deps.started_env)
    assert any(env.get("ENGINE_LOCAL_ICE_SERVERS") == "" for env in deps.started_env)
    assert any(env.get("ENGINE_PUBLIC_ICE_SERVERS") == "" for env in deps.started_env)


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
        run_verification(config(evidence_dir=tmp_path, skip_tests=False, require_engine_binary=False), deps)


def test_fragment_browser_url_contains_no_query_api_or_token_log_payload(tmp_path):
    deps = FakeDeps()
    run_verification(config(evidence_dir=tmp_path), deps)

    assert deps.opened
    url = deps.opened[0]
    assert "?" not in url
    assert "#" in url
    assert "engine-select" not in url
    assert "Authorization" not in url


def test_engine_ice_and_signaling_environment_is_cleared_then_restored(monkeypatch):
    monkeypatch.setenv("ENGINE_LOCAL_ICE_SERVERS", "stale-local")
    monkeypatch.setenv("ENGINE_PUBLIC_ICE_SERVERS", "stale-public")
    monkeypatch.setenv("ENGINE_SIGNALING_SECRET", "stale-secret")
    deps = FakeDeps()

    run_verification(config(), deps)

    assert os.environ["ENGINE_LOCAL_ICE_SERVERS"] == "stale-local"
    assert os.environ["ENGINE_PUBLIC_ICE_SERVERS"] == "stale-public"
    assert os.environ["ENGINE_SIGNALING_SECRET"] == "stale-secret"


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


def test_standalone_runner_can_import_src_without_pytest_pythonpath(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    evidence_dir = tmp_path / "standalone-evidence"
    script = f"""
from pathlib import Path
from scripts.verify_python_orchestration import RealDeps, VerificationConfig

config = VerificationConfig(
    repo_root=Path.cwd(),
    engine_exe=Path.cwd() / "engine.exe",
    evidence_dir=Path({str(evidence_dir)!r}),
    enforce_windows=False,
)
RealDeps(config).discover_vms()
print("standalone discovery imported src")
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
    assert "standalone discovery imported src" in completed.stdout


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
    response_path = evidence_dir / "prompt-response.json"
    answers = {}

    def ask(key, checkpoint, message):
        try:
            answers[key] = deps.prompt(message, checkpoint=checkpoint)
        except BaseException as error:
            answers[key] = error

    first = threading.Thread(
        target=ask,
        args=("first", "first peer", "Confirm first peer video"),
        daemon=True,
    )
    first.start()
    wait_until(prompt_path.exists)
    assert (
        r".\engine\verify-python-orchestration.ps1 -Confirm PASS"
        in capsys.readouterr().out
    )
    first_prompt = json.loads(prompt_path.read_text(encoding="utf-8"))
    assert first_prompt == {
        "version": 1,
        "verifier_pid": os.getpid(),
        "verifier_started_at": first_prompt["verifier_started_at"],
        "nonce": first_prompt["nonce"],
        "checkpoint": "first peer",
        "message": "Confirm first peer video",
        "expected_results": ["PASS", "FAIL"],
    }
    assert first_prompt["nonce"]
    assert "real-auth-secret-must-not-appear" not in json.dumps(first_prompt)

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

    assert not (dead_dir / "prompt-response.json").exists()
    assert json.loads(
        (live_dir / "prompt-response.json").read_text(encoding="utf-8")
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

    assert not (evidence_dir / "prompt-response.json").exists()


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
    response_path = evidence_dir / "prompt-response.json"
    response_path.write_text(
        json.dumps({**prompt, "result": "PASS"}), encoding="utf-8"
    )
    monkeypatch.setattr(verifier, "_pid_started_at", lambda pid: 100.0)

    with pytest.raises(VerificationError, match="confirmation already submitted"):
        submit_file_confirmation(repo_root, "FAIL")

    assert json.loads(response_path.read_text(encoding="utf-8"))["result"] == "PASS"


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
    assert not list(test_root.glob("verification-*/prompt-response.json"))


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
