"""Dependency-injected Windows acceptance runner for engine orchestration.

The command-line PowerShell wrapper intentionally contains no lifecycle policy.
This module owns the test state machine so Darwin tests can exercise every
branch with fakes while Windows runs use the real command, ADB, process, HTTP,
and browser adapters.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import math
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx


_ADB_STDERR_LIMIT = 240
_SENSITIVE_ADB_STDERR = re.compile(
    r"(?i)\b((?:[a-z][a-z0-9_]*(?:token|secret|password|key)[a-z0-9_]*|"
    r"token|secret|password|key))\s*([=:])\s*"
    r"(?:bearer\s+)?\S+"
)
_AUTHORIZATION_ADB_STDERR = re.compile(
    r"(?i)\b(authorization)\s*:\s*(?:bearer\s+)?\S+"
)


class VerificationError(RuntimeError):
    pass


def _safe_adb_stderr(stderr: str | bytes | None) -> str:
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    detail = " ".join((stderr or "").split())
    detail = _SENSITIVE_ADB_STDERR.sub(r"\1\2<redacted>", detail)
    detail = _AUTHORIZATION_ADB_STDERR.sub(r"\1: <redacted>", detail)
    if len(detail) > _ADB_STDERR_LIMIT:
        return detail[:_ADB_STDERR_LIMIT] + "..."
    return detail


@dataclass
class VerificationConfig:
    repo_root: Path
    engine_exe: Path
    evidence_dir: Path
    serial: str | None = None
    tier: str = "720"
    relay_port: int = 8443
    page_port: int = 8090
    app_port: int = 8080
    skip_build: bool = True
    skip_tests: bool = True
    skip_expiry: bool = True
    keep_on_failure: bool = False
    enforce_windows: bool = True
    require_engine_binary: bool = True
    poll_seconds: float = 1.0
    expiry_poll_seconds: float = 30.0
    expiry_grace_seconds: float = 1.0
    file_prompts: bool = False
    file_prompt_poll_seconds: float = 0.25


@dataclass
class VerificationResult:
    status: str = "PASS"
    checkpoints: dict[str, dict[str, str]] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    def mark(self, name: str, status: str, detail: str = "") -> None:
        self.checkpoints[name] = {"status": status, "detail": detail}
        if status == "FAIL":
            self.status = "FAIL"
        elif status == "SKIP" and self.status == "PASS":
            self.status = "INCOMPLETE"


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    try:
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json_once(path: Path, payload: dict[str, Any]) -> None:
    """Atomically publish a response without replacing another confirmer's."""
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    try:
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise VerificationError("confirmation already submitted for this prompt") from error
    finally:
        temporary.unlink(missing_ok=True)


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


class FilePromptChannel:
    PROMPT_FILENAME = "active-prompt.json"
    RESPONSE_PREFIX = "prompt-response-"

    def __init__(
        self,
        evidence_dir: Path,
        *,
        verifier_pid: int | None = None,
        poll_seconds: float = 0.25,
        record_event: Callable[[str], None] | None = None,
    ):
        self.evidence_dir = evidence_dir
        self.verifier_pid = verifier_pid if verifier_pid is not None else os.getpid()
        self.verifier_started_at = _pid_started_at(self.verifier_pid)
        if self.verifier_started_at is None:
            raise VerificationError(
                "could not determine the live verifier process start time"
            )
        self.poll_seconds = poll_seconds
        self.record_event = record_event
        self.prompt_path = evidence_dir / self.PROMPT_FILENAME

    def _event(self, message: str) -> None:
        if self.record_event is not None:
            self.record_event(message)

    @classmethod
    def response_path(cls, evidence_dir: Path, nonce: str) -> Path:
        digest = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
        return evidence_dir / f"{cls.RESPONSE_PREFIX}{digest}.json"

    def _remove_artifact(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            self._event(f"File prompt cleanup unavailable for {path.name}: {error}")

    def cleanup(self) -> None:
        self._remove_artifact(self.prompt_path)
        for response_path in self.evidence_dir.glob(f"{self.RESPONSE_PREFIX}*.json"):
            self._remove_artifact(response_path)

    def prompt(self, message: str, checkpoint: str) -> str:
        self.cleanup()
        nonce = secrets.token_hex(16)
        prompt = {
            "version": 1,
            "verifier_pid": self.verifier_pid,
            "verifier_started_at": self.verifier_started_at,
            "nonce": nonce,
            "checkpoint": checkpoint,
            "message": message,
            "expected_results": ["PASS", "FAIL"],
        }
        _write_json_atomic(self.prompt_path, prompt)
        response_path = self.response_path(self.evidence_dir, nonce)
        notice = (
            f"CHECKPOINT: {checkpoint}\n"
            f"{message}\n\n"
            "Waiting for file confirmation. In a second terminal run: "
            ".\\engine\\verify-python-orchestration.ps1 -Confirm PASS "
            "(or -Confirm FAIL)"
        )
        print(notice, flush=True)
        self._event(notice)
        try:
            while True:
                if response_path.exists():
                    response = _read_json_object(response_path)
                    self._remove_artifact(response_path)
                    if (
                        response is not None
                        and response.get("version") == 1
                        and response.get("verifier_pid") == self.verifier_pid
                        and response.get("verifier_started_at")
                        == self.verifier_started_at
                        and response.get("nonce") == nonce
                        and response.get("result") in {"PASS", "FAIL"}
                    ):
                        result = str(response["result"])
                        self._event(
                            f"File confirmation received for checkpoint "
                            f"'{checkpoint}': {result}"
                        )
                        return result
                    self._event(
                        f"Ignored stale or mismatched file confirmation for "
                        f"checkpoint '{checkpoint}'"
                    )
                time.sleep(self.poll_seconds)
        finally:
            self.cleanup()


def _valid_prompt(value: dict[str, Any] | None) -> bool:
    return bool(
        value is not None
        and value.get("version") == 1
        and type(value.get("verifier_pid")) is int
        and value["verifier_pid"] > 0
        and type(value.get("verifier_started_at")) in {int, float}
        and value["verifier_started_at"] > 0
        and isinstance(value.get("nonce"), str)
        and bool(value["nonce"])
        and isinstance(value.get("checkpoint"), str)
        and isinstance(value.get("message"), str)
        and value.get("expected_results") == ["PASS", "FAIL"]
    )


def _pid_started_at(pid: int) -> float | None:
    import psutil

    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return None


def submit_file_confirmation(repo_root: Path, result: str) -> Path:
    if result not in {"PASS", "FAIL"}:
        raise VerificationError("file confirmation must be PASS or FAIL")

    active: list[tuple[Path, dict[str, Any]]] = []
    prompt_glob = repo_root / "engine" / "test"
    for prompt_path in prompt_glob.glob(
        f"verification-*/{FilePromptChannel.PROMPT_FILENAME}"
    ):
        prompt = _read_json_object(prompt_path)
        if not _valid_prompt(prompt):
            continue
        if _pid_started_at(prompt["verifier_pid"]) == prompt["verifier_started_at"]:
            active.append((prompt_path, prompt))

    if not active:
        raise VerificationError("no live active file prompt found")
    if len(active) != 1:
        raise VerificationError(
            f"multiple live active file prompts found ({len(active)}); "
            "stop all but one verifier run"
        )

    prompt_path, prompt = active[0]
    current = _read_json_object(prompt_path)
    if (
        not _valid_prompt(current)
        or current.get("verifier_pid") != prompt["verifier_pid"]
        or current.get("verifier_started_at") != prompt["verifier_started_at"]
        or current.get("nonce") != prompt["nonce"]
        or _pid_started_at(prompt["verifier_pid"])
        != prompt["verifier_started_at"]
    ):
        raise VerificationError("active file prompt changed; retry confirmation")
    response_path = FilePromptChannel.response_path(
        prompt_path.parent, prompt["nonce"]
    )
    _write_json_once(
        response_path,
        {
            "version": 1,
            "verifier_pid": prompt["verifier_pid"],
            "verifier_started_at": prompt["verifier_started_at"],
            "nonce": prompt["nonce"],
            "result": result,
        },
    )
    return response_path


def _strip_serial(value: str) -> str:
    return value[4:] if value.startswith("adb:") else value


def _python_script_argument(cmdline: list[Any]) -> str | None:
    if not cmdline:
        return None
    executable = str(cmdline[0]).replace("\\", "/").rsplit("/", 1)[-1].casefold()
    if executable.endswith(".exe"):
        executable = executable[:-4]
    prefix = "pythonw" if executable.startswith("pythonw") else "python"
    if not executable.startswith(prefix):
        return None
    version = executable[len(prefix):]
    if version and not all(part.isdigit() for part in version.split(".")):
        return None

    option_values = {"-W", "-X", "--check-hash-based-pycs"}
    index = 1
    while index < len(cmdline):
        argument = str(cmdline[index])
        if argument == "--":
            index += 1
            return str(cmdline[index]) if index < len(cmdline) else None
        if argument in {"-c", "-m"} or argument.startswith(("-c=", "-m=")):
            return None
        if argument in option_values:
            index += 2
            continue
        if argument.startswith("-"):
            index += 1
            continue
        return None if argument == "-" else argument
    return None


def _resolved_casefold_path(value: str, cwd: str | None = None) -> str | None:
    normalized = value.replace("\\", os.sep).replace("/", os.sep)
    path = Path(normalized)
    if not path.is_absolute():
        if not cwd:
            return None
        normalized_cwd = cwd.replace("\\", os.sep).replace("/", os.sep)
        path = Path(normalized_cwd) / path
    try:
        resolved = path.resolve(strict=False)
    except (OSError, RuntimeError):
        return None
    return str(resolved).replace("\\", "/").casefold()


def _fragment(selection: dict[str, Any]) -> str:
    # Fragments never reach the static server's request line/log. Do not put
    # credentials in the query string or in evidence files.
    payload = {
        "whep_url": selection["whep_url"],
        "whep_token": selection["whep_token"],
        "generation": selection["generation"],
        "width": selection.get("w", selection.get("width")),
        "height": selection.get("h", selection.get("height")),
        "ice_servers": selection.get("ice_servers", []),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    return "#" + encoded


def _safe_selection(selection: dict[str, Any]) -> dict[str, Any]:
    return {
        key: selection.get(key)
        for key in ("whep_url", "generation", "w", "h", "width", "height")
        if key in selection
    }


def _loop_until(
    deps: Any,
    predicate: Callable[[], bool],
    *,
    timeout: float,
    description: str,
    interval: float,
) -> None:
    deadline = deps.clock() + timeout
    while not predicate():
        remaining = deadline - deps.clock()
        if remaining <= 0:
            raise VerificationError(f"timed out waiting for {description}")
        deps.sleep(min(interval, remaining))


def _is_loopback(url: str) -> bool:
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname
    try:
        return bool(host and ipaddress.ip_address(host).is_loopback)
    except ValueError:
        return host in {"localhost"}


def _record_command(deps: Any, command: list[str], label: str) -> None:
    # RealDeps writes full command/output to disk; fakes retain the command
    # tuple so behavior tests can assert the boundary without source-grepping.
    deps.record_command(command, label)


def _trace(deps: Any, message: str) -> None:
    recorder = getattr(deps, "record_event", None)
    if recorder is not None:
        recorder(message)


def _progress(deps: Any, message: str) -> None:
    reporter = getattr(deps, "report_progress", None)
    if reporter is not None:
        reporter(message)
    else:
        _trace(deps, message)


def _ask(deps: Any, result: VerificationResult, name: str, message: str) -> bool:
    answer = deps.prompt(message, checkpoint=name)
    if answer != "PASS":
        result.mark(name, "FAIL", f"operator response: {answer}")
        return False
    result.mark(name, "PASS", "operator confirmed")
    return True


def _sole_ready_adb_device(deps: Any) -> str:
    ready_devices = deps.adb_devices()
    if len(ready_devices) != 1:
        raise VerificationError(
            f"expected exactly one ADB device in state device, found {len(ready_devices)}"
        )
    return ready_devices[0]


def run_verification(config: VerificationConfig, deps: Any) -> VerificationResult:
    result = VerificationResult()
    if config.enforce_windows and platform.system() != "Windows":
        raise VerificationError("Windows Host PC required; Windows integration is not verified here")

    # Refuse to attach to a process the runner did not create. This happens
    # before build, relay, app, browser, or ADB state changes.
    existing = list(deps.list_engine_processes())
    if existing:
        raise VerificationError("pre-existing engine.exe process found; close it before retrying")
    existing_apps = list(deps.list_app_processes())
    if existing_apps:
        raise VerificationError(
            "pre-existing WindowControl app src/main.py process found; close it before retrying"
        )

    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "ENGINE_EXE_PATH": str(config.engine_exe),
        "ENGINE_WHEP_CAPABILITY_SECRET": __import__("secrets").token_hex(32),
        "ENGINE_SIGNALING_SECRET": "",
        "ENGINE_LOCAL_ICE_SERVERS": "",
        "ENGINE_PUBLIC_ICE_SERVERS": "",
        "VPS_SIGNALING_URL": f"ws://127.0.0.1:{config.relay_port}",
        "AUTH_TOKEN": "",
        "PUBLIC_UI_URL": "",
        "TUNNEL_SECRET": "",
        "JWT_SECRET": "",
        "PORT": str(config.relay_port),
    })
    ready_device = _sole_ready_adb_device(deps)
    if config.serial and config.serial != ready_device:
        raise VerificationError(
            f"selected serial is not the sole ready ADB device: {config.serial}"
        )

    if config.skip_build is False:
        _record_command(deps, ["cmake", "--build", str(config.repo_root / "engine" / "build"), "--config", "Release"], "engine build")
        deps.run(
            ["cmake", "--build", str(config.repo_root / "engine" / "build"), "--config", "Release"],
            cwd=config.repo_root, env=env, label="engine build",
        )
    else:
        result.mark("engine build", "SKIP", "explicit --skip-build")
    if config.require_engine_binary and not config.engine_exe.exists():
        raise VerificationError(f"missing engine executable: {config.engine_exe}")

    if config.skip_tests is False:
        phase = [
            "tests/test_scrcpy_server.py", "tests/test_engine_process.py",
            "tests/test_engine_auth.py", "tests/test_engine_admin.py",
            "tests/test_engine_runtime.py", "tests/test_engine_orchestrator.py",
            "tests/test_scrcpy_session.py", "tests/test_instance_manager.py",
            "tests/test_app.py", "tests/test_main.py",
        ]
        command = ["uv", "run", "pytest", *phase, "-v"]
        _record_command(deps, command, "phase-specific Python tests")
        deps.run(command, cwd=config.repo_root, env=env, label="phase-specific Python tests")
        engine_tests = config.repo_root / "engine" / "build" / "Release" / "engine_tests.exe"
        if not engine_tests.exists():
            raise VerificationError(f"missing engine_tests.exe: {engine_tests}")
        command = [str(engine_tests), "--gtest_filter=-SignalingClient.*:PublicSignalingBridge.*"]
        _record_command(deps, command, "engine tests")
        deps.run(command, cwd=config.repo_root, env=env, label="engine tests")
    else:
        result.mark("local tests", "SKIP", "explicit --skip-tests")

    current_ready_device = _sole_ready_adb_device(deps)
    if current_ready_device != ready_device:
        raise VerificationError(
            "sole ready ADB device changed before discovery: "
            f"{ready_device} -> {current_ready_device}"
        )
    vms = list(deps.discover_vms())
    if config.serial:
        vm = next((item for item in vms if _strip_serial(item["id"]) == config.serial), None)
    else:
        candidates = [
            item for item in vms if _strip_serial(item["id"]) == ready_device
        ]
        if len(candidates) != 1:
            raise VerificationError(f"expected one ready discovered emulator, found {len(candidates)}")
        vm = candidates[0]
    if vm is None:
        raise VerificationError(f"selected serial not present in repository discovery: {config.serial}")
    serial = _strip_serial(vm["id"])
    if deps.adb_state(serial) != "device":
        raise VerificationError(f"ADB serial is not ready: {serial}")
    index = int(vm["ldplayer_index"])
    scrcpy_port = 27183 + index
    scid = index
    result.summary.update({"serial": serial, "ldplayer_index": index, "scrcpy_port": scrcpy_port, "scid": scid})
    _trace(deps, f"selected serial={serial} ldplayer_index={index} scrcpy_port={scrcpy_port} scid={scid}")

    owned: list[Any] = []
    owned_engine_pids: set[int] = set()
    app = None
    current_gate = "startup"
    try:
        relay = deps.start(
            ["node", "server.js"],
            cwd=config.repo_root / "infra" / "vps" / "signaling",
            env=env, stdout_path=config.evidence_dir / "relay.log", label="local signaling relay",
        )
        app = deps.start(
            ["uv", "run", "python", "src/main.py"], cwd=config.repo_root, env=env,
            stdout_path=config.evidence_dir / "app.log", label="WindowControl app",
        )
        owned.extend((relay, app))

        base = f"http://127.0.0.1:{config.app_port}"
        instances: list[dict[str, Any]] = []

        def discovery_ready() -> bool:
            nonlocal instances
            exit_code = app.poll()
            if exit_code is not None:
                raise VerificationError(
                    f"WindowControl app exited during discovery with exit code {exit_code}"
                )
            try:
                instances = deps.api_instances()
            except httpx.TransportError as error:
                exit_code = app.poll()
                if exit_code is not None:
                    raise VerificationError(
                        f"WindowControl app exited during discovery with exit code {exit_code}"
                    ) from error
                _trace(
                    deps,
                    f"WindowControl discovery transport error; retrying: {type(error).__name__}: {error}",
                )
                return False
            exit_code = app.poll()
            if exit_code is not None:
                raise VerificationError(
                    f"WindowControl app exited during discovery with exit code {exit_code}"
                )
            return bool(
                instances
                and any(item.get("serial") == serial for item in instances)
            )

        _loop_until(
            deps, discovery_ready,
            timeout=90, description="WindowControl discovery", interval=config.poll_seconds,
        )
        engine_processes = list(deps.list_engine_processes())
        current_gate = "discovery"
        if len(engine_processes) != 1:
            raise VerificationError(f"discovery started {len(engine_processes)} engine.exe processes, expected one")
        owned_engine_pids = {int(item.pid) for item in engine_processes}
        result.mark("discovery", "PASS", f"owned engine pid {sorted(owned_engine_pids)[0]}")

        current_gate = "baseline quality"
        deps.api_quality(serial, config.tier)
        _trace(deps, f"baseline quality target={config.tier}")

        selection = deps.api_select(serial)
        current_gate = "selection"
        if not selection.get("whep_token"):
            raise VerificationError("engine-select returned no WHEP token")
        if _is_loopback(selection["whep_url"]):
            raise VerificationError("engine-select returned a loopback WHEP URL")
        result.summary["initial_selection"] = _safe_selection(selection)
        result.mark("selection", "PASS", selection["whep_url"])

        page = deps.start(
            ["uv", "run", "python", "-m", "http.server", str(config.page_port), "--directory", "engine/test"],
            cwd=config.repo_root, env=env, stdout_path=config.evidence_dir / "page.log", label="verifier page server",
        )
        owned.append(page)
        page_url = f"http://127.0.0.1:{config.page_port}/python_orchestration_verifier.html{_fragment(selection)}"
        deps.open_browser(page_url)
        result.summary["initial_page"] = page_url.split("#", 1)[0] + "#<redacted>"
        if not _ask(
            deps,
            result,
            "first peer",
            "First peer checklist:\n"
            "- Confirm non-black, changing live video.\n"
            "- Confirm framesDecoded is increasing and the DataChannel is open.\n"
            "- Click the video and confirm the click affects the device.\n"
            "Type PASS only after every item passes.",
        ):
            return result

        if config.skip_expiry:
            result.mark("expiry", "SKIP", "explicit --skip-expiry")
        else:
            try:
                expiry = int(selection["whep_token"].split(".", 1)[0])
            except (KeyError, ValueError):
                raise VerificationError("WHEP token has no parseable expiry")
            expiry_deadline = expiry + config.expiry_grace_seconds
            remaining = max(0, math.ceil(expiry_deadline - deps.clock()))
            _progress(
                deps,
                f"WHEP token expiry wait: {remaining} seconds remaining",
            )
            while deps.clock() < expiry_deadline:
                deps.sleep(
                    min(
                        config.expiry_poll_seconds,
                        expiry_deadline - deps.clock(),
                    )
                )
                remaining = max(0, math.ceil(expiry_deadline - deps.clock()))
                if remaining:
                    _progress(
                        deps,
                        f"WHEP token expiry wait: {remaining} seconds remaining",
                    )
            _progress(deps, "WHEP token expiry wait: complete")
            fresh = deps.api_select(serial)
            if fresh["whep_token"] == selection["whep_token"]:
                raise VerificationError("post-expiry selection returned the same token")
            fresh_page = f"http://127.0.0.1:{config.page_port}/python_orchestration_verifier.html{_fragment(fresh)}"
            deps.open_browser(fresh_page)
            result.mark("expiry", "PASS", "fresh token opened in an independent verifier page")
            if not _ask(
                deps,
                result,
                "second peer",
                "Second peer checklist:\n"
                "- Confirm the fresh verifier page independently has live video.\n"
                "- Confirm its framesDecoded is increasing.\n"
                "- Confirm the original peer remains live.\n"
                "Type PASS only after every item passes.",
            ):
                return result

        before_quality = selection
        current_gate = "quality"
        target_tier = "720" if config.tier == "480" else "480"
        deps.api_quality(serial, target_tier)
        quality = None

        def quality_ready() -> bool:
            nonlocal quality
            quality = deps.api_select(serial)
            _trace(deps, f"quality poll generation={quality.get('generation')} dimensions={quality.get('w')}x{quality.get('h')}")
            return quality["generation"] > before_quality["generation"] and quality.get("w") != before_quality.get("w")

        _loop_until(deps, quality_ready, timeout=45, description="quality reconnect generation/dimensions", interval=config.poll_seconds)
        if quality["whep_url"] != before_quality["whep_url"]:
            raise VerificationError("quality reconnect replaced the WHEP endpoint")
        if {int(item.pid) for item in deps.list_engine_processes()} != owned_engine_pids:
            raise VerificationError("quality reconnect changed the owned engine process")
        _trace(deps, f"quality invariant pid_set={sorted(owned_engine_pids)} endpoint={quality['whep_url']}")
        result.summary["quality"] = _safe_selection(quality)
        if not _ask(
            deps,
            result,
            "quality",
            "Quality checklist:\n"
            "- Confirm every existing peer stayed connected.\n"
            "- Confirm the shown dimensions changed.\n"
            "- Confirm live video and increasing framesDecoded continue without reloading.\n"
            "Type PASS only after every item passes.",
        ):
            return result

        before_source = quality
        current_gate = "scrcpy recovery"
        deps.kill_scrcpy(serial, scid)
        source = None

        def source_ready() -> bool:
            nonlocal source
            source = deps.api_select(serial)
            _trace(deps, f"scrcpy recovery poll generation={source.get('generation')}")
            return source["generation"] > before_source["generation"]

        _loop_until(deps, source_ready, timeout=45, description="scrcpy watchdog recovery", interval=config.poll_seconds)
        if {int(item.pid) for item in deps.list_engine_processes()} != owned_engine_pids:
            raise VerificationError("scrcpy recovery changed the owned engine process")
        if source["whep_url"] != before_source["whep_url"]:
            raise VerificationError("scrcpy recovery changed the WHEP endpoint")
        if not deps.adb_forwards(serial, scrcpy_port):
            raise VerificationError("scrcpy recovery lost its ADB forward")
        result.summary["source_recovery"] = _safe_selection(source)
        if not _ask(
            deps,
            result,
            "scrcpy recovery",
            "Scrcpy recovery checklist:\n"
            "- Confirm the same existing peers resume live video.\n"
            "- Confirm framesDecoded increases again.\n"
            "- Confirm this happens without reloading either page.\n"
            "Type PASS only after every item passes.",
        ):
            return result

        old_engine_pid = next(iter(owned_engine_pids))
        current_gate = "engine respawn"
        deps.kill_owned_engine(old_engine_pid)
        respawn = None

        def respawn_ready() -> bool:
            nonlocal respawn
            candidates = list(deps.list_engine_processes())
            if len(candidates) != 1 or int(candidates[0].pid) == old_engine_pid:
                return False
            respawn = deps.api_select(serial)
            _trace(deps, f"engine respawn poll pids={[int(item.pid) for item in candidates]} whep={respawn.get('whep_url')}")
            return respawn["whep_url"] != source["whep_url"]

        _loop_until(deps, respawn_ready, timeout=60, description="engine respawn and dynamic WHEP port", interval=config.poll_seconds)
        owned_engine_pids = {int(item.pid) for item in deps.list_engine_processes()}
        if not deps.adb_forwards(serial, scrcpy_port):
            raise VerificationError("engine respawn did not retain exactly this instance's scrcpy forward")
        result.summary["respawn"] = _safe_selection(respawn)
        fresh_respawn_page = f"http://127.0.0.1:{config.page_port}/python_orchestration_verifier.html{_fragment(respawn)}"
        deps.open_browser(fresh_respawn_page)
        if not _ask(
            deps,
            result,
            "engine respawn",
            "Engine respawn checklist:\n"
            "- Confirm the fresh verifier page has live video with increasing framesDecoded.\n"
            "- Confirm its DataChannel is open.\n"
            "- Click the fresh page and confirm the click affects the device.\n"
            "Type PASS only after every item passes.",
        ):
            return result

        current_gate = "emulator removal"
        # Kill the source immediately before removal, so this gate proves
        # removal while recovery is active rather than after a healthy idle.
        deps.kill_scrcpy(serial, scid)
        _trace(deps, "removal ordering: scrcpy source loss triggered before device removal")
        def manual_removal_fallback(error: VerificationError) -> bool:
            if deps.prompt(
                "Automatic removal checklist:\n"
                f"- Automatic removal of selected device {serial} did not complete: {error}\n"
                f"- Manually remove or disconnect only the selected device {serial}; do not alter other devices.\n"
                "- Wait until that selected device disappears, then type PASS.",
                checkpoint="emulator removal",
            ) != "PASS":
                result.mark("emulator removal", "FAIL", "automatic and manual removal failed")
                return False
            return True

        manual_removal_required = False
        try:
            deps.remove_device(serial, index)
        except VerificationError as error:
            manual_removal_required = True
            if not manual_removal_fallback(error):
                return result
        if not manual_removal_required:
            try:
                _loop_until(deps, lambda: serial not in deps.adb_devices(), timeout=30, description="selected emulator removal", interval=config.poll_seconds)
            except VerificationError as error:
                manual_removal_required = True
                if not manual_removal_fallback(error):
                    return result
        if manual_removal_required:
            _loop_until(deps, lambda: serial not in deps.adb_devices(), timeout=30, description="selected emulator manual removal", interval=config.poll_seconds)
        _loop_until(deps, lambda: not (owned_engine_pids & {int(item.pid) for item in deps.list_engine_processes()}), timeout=30, description="removed instance engine cleanup", interval=config.poll_seconds)
        _loop_until(deps, lambda: not any(item.get("serial") == serial for item in deps.api_instances()), timeout=30, description="API removal of selected instance", interval=config.poll_seconds)
        if deps.adb_forwards(serial, scrcpy_port):
            raise VerificationError("selected emulator removal left an ADB forward")
        if not _ask(
            deps,
            result,
            "emulator removal",
            "Emulator removal checklist:\n"
            "- Confirm the selected instance is absent.\n"
            "- Confirm its engine process is absent.\n"
            "- Confirm its selected ADB forward is absent.\n"
            "Type PASS only after every item passes.",
        ):
            return result

        current_gate = "application exit"
        if deps.prompt(
            "Application exit checklist:\n"
            "- Use WindowControl's tray Exit now; do not force-kill the app.\n"
            "- Confirm the app, engine, and selected ADB forward are gone.\n"
            "Type PASS only after every item passes.",
            checkpoint="application exit",
        ) != "PASS":
            result.mark("application exit", "FAIL", "operator did not confirm tray Exit")
            return result
        try:
            _loop_until(deps, lambda: not deps.list_app_processes(), timeout=30, description="operator tray Exit", interval=config.poll_seconds)
        except VerificationError as error:
            result.mark("application exit", "FAIL", str(error))
            return result
        if deps.list_engine_processes():
            result.mark("application exit", "FAIL", "engine process remains after tray Exit")
            return result
        if deps.adb_forwards(serial, scrcpy_port):
            result.mark("application exit", "FAIL", "ADB forward remains after tray Exit")
            return result
        result.mark("application exit", "PASS", "tray Exit observed and owned engine processes gone")
        result.status = "INCOMPLETE" if any(item["status"] == "SKIP" for item in result.checkpoints.values()) else result.status
        result.summary["status"] = result.status
        return result
    except Exception as error:
        result.mark(current_gate, "FAIL", str(error))
        result.summary["failed_gate"] = current_gate
        result.summary["error"] = str(error)
        return result
    finally:
        cleanup_prompts = getattr(deps, "cleanup_prompts", None)
        if cleanup_prompts is not None:
            try:
                cleanup_prompts()
            except OSError as error:
                _progress(deps, f"File prompt cleanup unavailable: {error}")
        # Never force-kill the WindowControl app. On an incomplete/failed run,
        # leave the app, owned engine, and selected forward for diagnostics when
        # requested; helper relay/page processes are safe to stop otherwise.
        failed = result.status in {"FAIL", "INCOMPLETE"}
        for process in owned:
            if process is not app:
                deps.stop_helper(process)
        if failed:
            retained_forward = False
            try:
                retained_forward = bool(deps.adb_forwards(serial, scrcpy_port))
            except Exception:
                pass
            result.summary["retained_on_failure"] = {
                "keep_on_failure": config.keep_on_failure,
                "app": app is not None,
                "owned_engine_pids": sorted(owned_engine_pids),
                "selected_forward": retained_forward,
            }
            _trace(deps, f"failure retention app={app is not None} engine_pids={sorted(owned_engine_pids)} selected_forward={retained_forward}")
        service_log = Path(r"C:\ProgramData\WindowControl\service_crash.log")
        if service_log.exists():
            try:
                shutil.copyfile(service_log, config.evidence_dir / "service_crash.log")
            except OSError:
                _trace(deps, "service log copy unavailable")


class RealDeps:
    """Windows adapters used by the PowerShell entry point."""

    def __init__(self, config: VerificationConfig):
        self.config = config
        self._log = config.evidence_dir / "commands.log"
        self._file_prompt_channel = (
            FilePromptChannel(
                config.evidence_dir,
                poll_seconds=config.file_prompt_poll_seconds,
                record_event=self.record_event,
            )
            if config.file_prompts
            else None
        )

    def record_command(self, command: list[str], label: str) -> None:
        with self._log.open("a", encoding="utf-8") as stream:
            stream.write(f"{label}: {' '.join(command)}\n")

    def record_event(self, message: str) -> None:
        with (self.config.evidence_dir / "verification.log").open("a", encoding="utf-8") as stream:
            stream.write(f"{time.time():.3f} {message}\n")

    def run(self, command, *, cwd, env, label):
        self.record_command(command, label)
        completed = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)
        with self._log.open("a", encoding="utf-8") as stream:
            stream.write(completed.stdout)
            stream.write(completed.stderr)
        if completed.returncode:
            raise VerificationError(f"{label} failed with exit code {completed.returncode}")

    def start(self, command, *, cwd, env, stdout_path, label):
        self.record_command(command, label)
        output = stdout_path.open("a", encoding="utf-8")
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT, text=True)
        process._verification_output = output
        return process

    def stop_helper(self, process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        output = getattr(process, "_verification_output", None)
        if output:
            output.close()

    def list_engine_processes(self):
        import psutil

        processes = [p for p in psutil.process_iter(["pid", "name", "cmdline"]) if (p.info.get("name") or "").lower() == "engine.exe"]
        return processes

    def discover_vms(self):
        source_root = str((self.config.repo_root / "src").resolve())
        if source_root not in sys.path:
            sys.path.insert(0, source_root)
        from server.adb_manager import list_vms

        return list_vms()

    def adb(self, args):
        self.record_command(["adb", *args], "adb")
        return subprocess.run(["adb", *args], text=True, capture_output=True, check=False)

    def adb_devices(self):
        result = self.adb(["devices"])
        devices = [line.split()[0] for line in result.stdout.splitlines() if len(line.split()) >= 2 and line.split()[1] == "device"]
        self.record_event(f"adb devices={devices}")
        return devices

    def adb_state(self, serial):
        return self.adb(["-s", serial, "get-state"]).stdout.strip()

    def adb_forwards(self, serial, port):
        completed = self.adb(["forward", "--list"])
        if completed.returncode:
            raise VerificationError(
                f"adb forward --list failed with exit code {completed.returncode}"
            )
        forwards = [
            line for line in completed.stdout.splitlines()
            if len(line.split()) >= 2
            and line.split()[0] == serial
            and line.split()[1] == f"tcp:{port}"
        ]
        self.record_event(f"adb forwards serial={serial} port={port} count={len(forwards)}")
        return forwards

    def kill_scrcpy(self, serial, scid):
        # discover_vms() has already added <repo>/src to sys.path before the
        # recovery gate, so this reuses the launcher contract without making
        # the standalone verifier import depend on PYTHONPATH at startup.
        from server.scrcpy_server import scrcpy_server_process_pattern

        completed = self.adb([
            "-s", serial, "shell",
            f"pkill -f '{scrcpy_server_process_pattern(scid)}'",
        ])
        if completed.returncode:
            detail = _safe_adb_stderr(completed.stderr)
            message = (
                "selected scrcpy-server kill failed "
                f"with exit code {completed.returncode}"
            )
            if detail:
                message += f": {detail}"
            raise VerificationError(message)

    def remove_device(self, serial, ldplayer_index):
        if serial.startswith("emulator-"):
            from server.adb_manager import _find_ldconsole, _no_window_flags

            ldconsole = _find_ldconsole()
            if not ldconsole:
                raise VerificationError(
                    "automatic emulator removal failed: ldconsole.exe is unavailable"
                )
            command = [ldconsole, "quit", "--index", str(ldplayer_index)]
            self.record_command(command, "selected LDPlayer quit")
            try:
                completed = subprocess.run(
                    command,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=15,
                    **_no_window_flags(),
                )
            except subprocess.TimeoutExpired as error:
                detail = _safe_adb_stderr(error.stderr)
                message = "selected LDPlayer quit timed out"
                if detail:
                    message += f": {detail}"
                raise VerificationError(message) from error
            except OSError as error:
                detail = _safe_adb_stderr(str(error))
                message = "selected LDPlayer quit could not start"
                if detail:
                    message += f": {detail}"
                raise VerificationError(message) from error
        else:
            completed = self.adb(["disconnect", serial])
        if completed.returncode:
            detail = _safe_adb_stderr(completed.stderr)
            message = (
                "automatic emulator removal failed "
                f"with exit code {completed.returncode}"
            )
            if detail:
                message += f": {detail}"
            raise VerificationError(message)

    def list_app_processes(self):
        import psutil

        target = _resolved_casefold_path(
            str(self.config.repo_root / "src" / "main.py")
        )
        processes = []
        for process in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
            try:
                info = process.info
            except psutil.Error:
                continue
            script = _python_script_argument(info.get("cmdline") or [])
            if script is None:
                continue
            if _resolved_casefold_path(script, info.get("cwd")) == target:
                processes.append(process)
        return processes

    def kill_owned_engine(self, pid):
        import psutil

        psutil.Process(pid).terminate()

    def api_instances(self):
        import httpx

        response = httpx.get(f"http://127.0.0.1:{self.config.app_port}/instances", timeout=3)
        response.raise_for_status()
        value = response.json()
        self.record_event(f"GET /instances count={len(value)}")
        return value

    def api_select(self, serial):
        import httpx

        response = httpx.post(f"http://127.0.0.1:{self.config.app_port}/instances/{serial}/engine-select", json={}, timeout=10)
        response.raise_for_status()
        value = response.json()
        self.record_event(f"POST /engine-select serial={serial} generation={value.get('generation')} whep={value.get('whep_url')}")
        return value

    def api_quality(self, serial, tier):
        import httpx

        response = httpx.post(f"http://127.0.0.1:{self.config.app_port}/instances/{serial}/quality", json={"tier": tier}, timeout=60)
        response.raise_for_status()
        value = response.json()
        self.record_event(f"POST /quality tier={tier} status={response.status_code}")
        return value

    def open_browser(self, url):
        webbrowser.open(url)

    def prompt(self, message, checkpoint=None):
        if self._file_prompt_channel is not None:
            return self._file_prompt_channel.prompt(
                message,
                checkpoint or "operator confirmation",
            )
        return input(message + ": ").strip()

    def cleanup_prompts(self):
        if self._file_prompt_channel is not None:
            self._file_prompt_channel.cleanup()

    def report_progress(self, message):
        print(message, flush=True)
        self.record_event(message)

    def clock(self):
        return time.time()

    def sleep(self, seconds):
        time.sleep(seconds)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--engine-exe", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--confirm", choices=("PASS", "FAIL"))
    parser.add_argument("--serial", default=None)
    parser.add_argument("--tier", default="720")
    parser.add_argument("--relay-port", type=int, default=8443)
    parser.add_argument("--page-port", type=int, default=8090)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-expiry", action="store_true")
    parser.add_argument("--keep-on-failure", action="store_true")
    parser.add_argument("--file-prompts", action="store_true")
    args = parser.parse_args()
    if args.confirm:
        try:
            response_path = submit_file_confirmation(args.repo_root, args.confirm)
        except VerificationError as error:
            print(f"FAIL: {error}")
            return 1
        print(
            f"Submitted {args.confirm} confirmation to "
            f"{response_path.parent.name}"
        )
        return 0
    if args.engine_exe is None or args.evidence_dir is None:
        parser.error("--engine-exe and --evidence-dir are required for verification")
    config = VerificationConfig(
        repo_root=args.repo_root, engine_exe=args.engine_exe, evidence_dir=args.evidence_dir,
        serial=args.serial, tier=args.tier, relay_port=args.relay_port, page_port=args.page_port,
        skip_build=args.skip_build, skip_tests=args.skip_tests,
        skip_expiry=args.skip_expiry, keep_on_failure=args.keep_on_failure,
        file_prompts=args.file_prompts,
    )
    deps = RealDeps(config)
    try:
        result = run_verification(config, deps)
    except VerificationError as error:
        result = VerificationResult(status="FAIL", summary={"error": str(error)})
        config.evidence_dir.mkdir(parents=True, exist_ok=True)
        (config.evidence_dir / "result.json").write_text(json.dumps(result.__dict__, indent=2), encoding="utf-8")
        print(f"FAIL: {error}")
        return 1
    except Exception as error:
        result = VerificationResult(status="FAIL", summary={"error": f"unexpected: {error}"})
        config.evidence_dir.mkdir(parents=True, exist_ok=True)
        (config.evidence_dir / "result.json").write_text(json.dumps(result.__dict__, indent=2), encoding="utf-8")
        print(f"FAIL: unexpected error: {error}")
        return 1
    result.summary["status"] = result.status
    (config.evidence_dir / "result.json").write_text(json.dumps(result.__dict__, indent=2), encoding="utf-8")
    print(json.dumps(result.__dict__, indent=2))
    return 0 if result.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
