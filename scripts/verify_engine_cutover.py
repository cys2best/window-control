"""Dependency-injected final acceptance runner for the engine-only cutover.

The state machine is deliberately separate from its Windows adapters.  Tests
run the complete matrix with deterministic fakes; the PowerShell entry point
uses :class:`RealCutoverDeps` for processes, ADB, HTTP, browsers, prompts, and
durable evidence.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, quote, urlsplit

import httpx


RECORDED_PERFORMANCE_OVERRIDE = (
    "skip five-instance validation; proceed with engine-only cutover"
)
_REQUIRED_SOAK_HOURS = 8
_REQUIRED_TIERS = ("480", "720", "1080", "1440", "480")
_SELECTION_RESPONSE_FIELDS = {
    "ok",
    "id",
    "serial",
    "name",
    "w",
    "h",
    "whep_url",
    "whep_token",
    "signaling_url",
    "signaling_token",
    "ice_servers",
    "generation",
}
_SOAK_FIELDS = {
    "sampled_at_seconds",
    "process_count",
    "peer_count",
    "forward_count",
    "cpu_percent",
    "rss_bytes",
    "frames_decoded",
}
_SENSITIVE = re.compile(
    r"(?i)((?:authorization\s*:\s*bearer|token|secret|password|credential)"
    r"\s*[=:]\s*)(?:bearer\s+)?[^\s,;\"']+"
)


class CutoverError(RuntimeError):
    pass


@dataclass(frozen=True)
class OwnedProcess:
    kind: str
    pid: int
    started_at: float


@dataclass
class CutoverConfig:
    repo_root: Path
    serials: tuple[str, ...]
    performance_evidence_dir: Path
    evidence_dir: Path
    public_signaling_url: str
    installer_path: Path
    performance_override: str | None = None
    soak_hours: float = 8
    sample_interval_seconds: int = 60
    handshake_timeout_seconds: float = 15
    app_port: int = 8080
    keep_on_failure: bool = False
    enforce_windows: bool = True
    file_prompts: bool = False
    file_prompt_poll_seconds: float = 0.25


@dataclass
class CutoverResult:
    status: str = "PASS"
    checkpoints: dict[str, dict[str, str]] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    def mark(self, name: str, status: str, detail: str = "") -> None:
        self.checkpoints[name] = {"status": status, "detail": detail}
        if status == "FAIL":
            self.status = "FAIL"
        elif status in {"SKIP", "INCOMPLETE"} and self.status == "PASS":
            self.status = "INCOMPLETE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "status": self.status,
            "checkpoints": self.checkpoints,
            "summary": self.summary,
        }


def _safe_detail(value: Any) -> str:
    detail = " ".join(str(value).split())
    detail = _SENSITIVE.sub(r"\1<redacted>", detail)
    return detail[:477] + "..." if len(detail) > 480 else detail


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    try:
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _pid_started_at(pid: int) -> float | None:
    import psutil

    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return None


class CutoverFilePromptChannel:
    """Phase-2-compatible nonce/PID/start-time-scoped file prompt channel."""

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
            raise CutoverError("could not determine live verifier process start time")
        self.poll_seconds = poll_seconds
        self.record_event = record_event
        self.prompt_path = evidence_dir / self.PROMPT_FILENAME

    @classmethod
    def response_path(cls, evidence_dir: Path, nonce: str) -> Path:
        digest = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
        return evidence_dir / f"{cls.RESPONSE_PREFIX}{digest}.json"

    def _event(self, message: str) -> None:
        if self.record_event is not None:
            self.record_event(message)

    def _remove(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            self._event(f"prompt cleanup unavailable for {path.name}: {_safe_detail(error)}")

    def cleanup(self) -> None:
        self._remove(self.prompt_path)
        for path in self.evidence_dir.glob(f"{self.RESPONSE_PREFIX}*.json"):
            self._remove(path)

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
            f"CHECKPOINT: {checkpoint}\n{message}\n\n"
            "Waiting for file confirmation. In a second terminal run: "
            r".\engine\verify-engine-cutover.ps1 -Confirm PASS"
            " (or -Confirm FAIL)"
        )
        print(notice, flush=True)
        self._event(notice)
        try:
            while True:
                if response_path.exists():
                    response = _read_json(response_path)
                    self._remove(response_path)
                    if (
                        response is not None
                        and response.get("version") == 1
                        and response.get("verifier_pid") == self.verifier_pid
                        and response.get("verifier_started_at") == self.verifier_started_at
                        and response.get("nonce") == nonce
                        and response.get("result") in {"PASS", "FAIL"}
                    ):
                        return str(response["result"])
                    self._event(f"ignored mismatched confirmation for {checkpoint}")
                time.sleep(self.poll_seconds)
        finally:
            self.cleanup()


def _valid_prompt(value: dict[str, Any] | None) -> bool:
    return bool(
        value
        and value.get("version") == 1
        and type(value.get("verifier_pid")) is int
        and value["verifier_pid"] > 0
        and type(value.get("verifier_started_at")) in {int, float}
        and value["verifier_started_at"] > 0
        and isinstance(value.get("nonce"), str)
        and value["nonce"]
        and isinstance(value.get("checkpoint"), str)
        and isinstance(value.get("message"), str)
        and value.get("expected_results") == ["PASS", "FAIL"]
    )


def submit_file_confirmation(repo_root: Path, result: str) -> Path:
    result = result.upper()
    if result not in {"PASS", "FAIL"}:
        raise CutoverError("file confirmation must be PASS or FAIL")
    active: list[tuple[Path, dict[str, Any]]] = []
    for prompt_path in (repo_root / "engine" / "test").glob(
        f"engine-cutover-*/{CutoverFilePromptChannel.PROMPT_FILENAME}"
    ):
        prompt = _read_json(prompt_path)
        if _valid_prompt(prompt) and _pid_started_at(prompt["verifier_pid"]) == prompt["verifier_started_at"]:
            active.append((prompt_path, prompt))
    if not active:
        raise CutoverError("no live active engine-cutover file prompt found")
    if len(active) != 1:
        raise CutoverError(f"multiple live engine-cutover prompts found ({len(active)})")
    prompt_path, prompt = active[0]
    current = _read_json(prompt_path)
    if (
        not _valid_prompt(current)
        or current.get("nonce") != prompt["nonce"]
        or current.get("verifier_pid") != prompt["verifier_pid"]
        or current.get("verifier_started_at") != prompt["verifier_started_at"]
        or _pid_started_at(prompt["verifier_pid"]) != prompt["verifier_started_at"]
    ):
        raise CutoverError("active file prompt changed; retry confirmation")
    response_path = CutoverFilePromptChannel.response_path(prompt_path.parent, prompt["nonce"])
    temporary = response_path.with_name(f".{response_path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_text(
            json.dumps(
                {
                    "version": 1,
                    "verifier_pid": prompt["verifier_pid"],
                    "verifier_started_at": prompt["verifier_started_at"],
                    "nonce": prompt["nonce"],
                    "result": result,
                }
            ),
            encoding="utf-8",
        )
        try:
            os.link(temporary, response_path)
        except FileExistsError as error:
            raise CutoverError("confirmation already submitted for this prompt") from error
    finally:
        temporary.unlink(missing_ok=True)
    return response_path


def _validate_serials(serials: tuple[str, ...]) -> None:
    if (
        len(serials) != 5
        or len(set(serials)) != 5
        or not all(isinstance(serial, str) and serial for serial in serials)
    ):
        raise CutoverError("expected exactly five unique ready ADB devices")


def _validate_ready_devices(expected: tuple[str, ...], actual: Any) -> tuple[str, ...]:
    devices = tuple(actual)
    _validate_serials(devices)
    if set(devices) != set(expected):
        raise CutoverError("ready ADB devices do not match the five supplied serials")
    return devices


def _validate_performance(config: CutoverConfig, result: CutoverResult) -> None:
    if config.performance_override is not None:
        if config.performance_override != RECORDED_PERFORMANCE_OVERRIDE:
            raise CutoverError("unrecognized performance override")
        result.mark("performance", "OVERRIDDEN", RECORDED_PERFORMANCE_OVERRIDE)
        result.summary["performance_gate"] = "OVERRIDDEN"
        return
    decision_path = config.performance_evidence_dir / "cutover-decision.json"
    decision = _read_json(decision_path)
    if decision is None:
        raise CutoverError("missing or invalid cutover-decision.json")
    if decision.get("schema_version") != 1 or decision.get("decision") not in {
        "APPROVE CUTOVER",
        "OVERRIDE CUTOVER",
    }:
        raise CutoverError("cutover-decision.json is not APPROVE/OVERRIDE")
    hashes = decision.get("result_hashes")
    if not isinstance(hashes, dict) or len(hashes) != 4:
        raise CutoverError("cutover-decision.json must contain four result hashes")
    combinations = set()
    serial_sets = set()
    root = config.performance_evidence_dir.resolve()
    from scripts.measure_engine_cutover import MeasurementError, _validate_completed_result

    for raw_path, expected_hash in hashes.items():
        path = Path(raw_path).resolve()
        if root not in path.parents or not path.is_file():
            raise CutoverError("performance result hash targets invalid evidence")
        actual_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if not isinstance(expected_hash, str) or actual_hash != expected_hash:
            raise CutoverError(f"performance result hash mismatch: {path.name}")
        payload = _read_json(path)
        try:
            _validate_completed_result(payload, path.name)
        except MeasurementError as error:
            raise CutoverError(
                f"performance result is not a complete schema-v1 PASS: {path.name}"
            ) from error
        combinations.add((payload["mode"], payload["workload"]))
        serials = payload.get("serials")
        if not isinstance(serials, list):
            raise CutoverError("performance serials are missing")
        serial_sets.add(tuple(serials))
    if combinations != {
        ("legacy", "no-viewer"),
        ("legacy", "one-viewer"),
        ("engine", "no-viewer"),
        ("engine", "one-viewer"),
    } or len(serial_sets) != 1:
        raise CutoverError("performance evidence is not the exact four-way matrix")
    status = "OVERRIDDEN" if decision["decision"] == "OVERRIDE CUTOVER" else "PASS"
    detail = _safe_detail(decision.get("reason") or "four hashed PASS results approved")
    result.mark("performance", status, detail)
    result.summary["performance_gate"] = status


def _audit_surface(config: CutoverConfig, deps: Any) -> None:
    surface = deps.audit_cutover_surface(config.repo_root, config.serials[0])
    expected = f"/instances/{config.serials[0]}/select"
    if surface.get("selection_path") != expected:
        raise CutoverError("verifier must use production /instances/{id}/select")
    forbidden = []
    for field in (
        "forbidden_routes",
        "legacy_processes",
        "legacy_dependencies",
        "legacy_assets",
    ):
        values = surface.get(field)
        if not isinstance(values, list):
            raise CutoverError(f"surface audit omitted {field}")
        forbidden.extend(str(value) for value in values)
    if forbidden:
        raise CutoverError("legacy cutover surface remains: " + ", ".join(forbidden[:12]))


def _sanitized_environment(config: CutoverConfig) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    environment["ENGINE_EXE_PATH"] = str(
        config.repo_root / "engine" / "build" / "Release" / "engine.exe"
    )
    environment["VPS_SIGNALING_URL"] = config.public_signaling_url
    return environment


def _require_fields(value: Any, required: set[str], label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or not required.issubset(value):
        raise CutoverError(f"{label} did not provide required observations")
    return value


def _manual_gate(deps: Any, result: CutoverResult, name: str, message: str) -> bool:
    response = str(deps.confirm(name, message)).upper()
    if response == "PASS":
        result.mark(name, "PASS", "operator confirmed complete checklist")
        return True
    if response in {"SKIP", "INCOMPLETE"}:
        result.mark(name, "INCOMPLETE", "operator skipped or shortened checkpoint")
        return False
    result.mark(name, "FAIL", "operator did not confirm complete checklist")
    return False


def _browser_observation(value: Any, label: str) -> None:
    observed = _require_fields(value, {"video", "data_channel", "drag", "scroll_delta"}, label)
    if not observed["video"] or not observed["data_channel"] or not observed["drag"]:
        raise CutoverError(f"{label} missing video/DataChannel drag evidence")
    delta = observed["scroll_delta"]
    if isinstance(delta, bool) or not isinstance(delta, (int, float)) or delta == 0:
        raise CutoverError(f"{label} missing proportional scroll evidence")


def _health(value: Any, expected_local: int, expected_public: bool) -> None:
    health = _require_fields(value, {"local_peers", "public_peer"}, "engine health")
    if health["local_peers"] != expected_local or health["public_peer"] is not expected_public:
        raise CutoverError(
            f"peer cleanup mismatch: expected local={expected_local} public={expected_public}"
        )


def _write_result(config: CutoverConfig, result: CutoverResult) -> None:
    result.summary["status"] = result.status
    _write_json_atomic(config.evidence_dir / "result.json", result.to_dict())


def _validate_selection(selection: Any, serial: str) -> dict[str, Any]:
    required = _SELECTION_RESPONSE_FIELDS | {"request_path"}
    value = _require_fields(selection, required, "selection")
    if set(value) != required:
        raise CutoverError("selection did not preserve the exact 12-field production contract")
    if value["request_path"] != f"/instances/{serial}/select":
        raise CutoverError("selection did not use production /instances/{id}/select")
    if value["ok"] is not True or value["serial"] != serial:
        raise CutoverError("selection identity did not match the requested production instance")
    for field in ("w", "h"):
        dimension = value[field]
        if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0:
            raise CutoverError("selection returned invalid production dimensions")
    if not isinstance(value["ice_servers"], list):
        raise CutoverError("selection returned invalid ICE server metadata")
    for url_field, token_field in (
        ("whep_url", "whep_token"),
        ("signaling_url", "signaling_token"),
    ):
        url = value[url_field]
        token = value[token_field]
        if not isinstance(url, str) or not url or not isinstance(token, str) or not token:
            raise CutoverError("selection omitted required endpoint capability")
        if token in url or "token=" in url.lower():
            raise CutoverError("WHEP/viewer token appeared in a selected URL")
    return value


def _require_exact_selection_response(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != _SELECTION_RESPONSE_FIELDS:
        raise CutoverError("production selection did not return the exact 12-field contract")
    return value


def _wait_for_health(
    deps: Any,
    serial: str,
    expected_local: int,
    expected_public: bool,
    timeout: float,
) -> None:
    deadline = deps.monotonic() + timeout
    while True:
        health = deps.engine_health(serial)
        if (
            health.get("local_peers") == expected_local
            and health.get("public_peer") is expected_public
        ):
            return
        remaining = deadline - deps.monotonic()
        if remaining <= 0:
            raise CutoverError(
                f"peer cleanup timed out waiting for local={expected_local} public={expected_public}"
            )
        deps.sleep(min(0.25, remaining))


def _validate_public_websocket(
    actual_url: Any,
    selection: dict[str, Any],
    configured_url: str,
) -> None:
    if not isinstance(actual_url, str) or not actual_url:
        raise CutoverError("public browser signaling request was not observed")
    actual = urlsplit(actual_url)
    expected = urlsplit(configured_url)
    actual_endpoint = (actual.scheme, actual.netloc, actual.path or "/")
    expected_endpoint = (expected.scheme, expected.netloc, expected.path or "/")
    query = parse_qsl(actual.query, keep_blank_values=True)
    expected_query = {
        "session": selection["name"],
        "role": "viewer",
        "token": selection["signaling_token"],
    }
    if (
        actual_endpoint != expected_endpoint
        or actual.fragment
        or len(query) != len(expected_query)
        or dict(query) != expected_query
    ):
        raise CutoverError("public browser signaling request did not match exact viewer query auth")


def _validate_switches(items: Any, timeout: float) -> None:
    if not isinstance(items, list) or len(items) != 20:
        raise CutoverError("rapid switch gate did not perform exactly 20 switches")
    for index, item in enumerate(items):
        item = _require_fields(item, {"index", "abandoned_reaped", "elapsed_seconds"}, "rapid switch")
        if item["index"] != index or not item["abandoned_reaped"]:
            raise CutoverError("rapid switch left an abandoned local peer")
        elapsed = item["elapsed_seconds"]
        if isinstance(elapsed, bool) or not isinstance(elapsed, (int, float)) or elapsed > timeout:
            raise CutoverError("rapid switch cleanup exceeded handshake timeout")


def _validate_quality(items: Any) -> None:
    if not isinstance(items, list) or [item.get("tier") for item in items if isinstance(item, dict)] != list(_REQUIRED_TIERS):
        raise CutoverError("quality ladder did not run 480->720->1080->1440->480")
    resource_ids = set()
    peer_ids = set()
    generations = []
    longest_dimensions = []
    for item in items:
        item = _require_fields(
            item,
            {"resource_id", "peer_id", "generation", "width", "height", "decoded_width", "decoded_height"},
            "quality observation",
        )
        if not all(
            isinstance(item[field], str) and item[field]
            for field in ("resource_id", "peer_id")
        ):
            raise CutoverError("quality observation omitted resource or peer identity")
        resource_ids.add(item["resource_id"])
        peer_ids.add(item["peer_id"])
        generations.append(item["generation"])
        numeric_dimensions = [
            item["width"], item["height"], item["decoded_width"], item["decoded_height"]
        ]
        if (
            any(
                isinstance(dimension, bool)
                or not isinstance(dimension, (int, float))
                or dimension <= 0
                for dimension in numeric_dimensions
            )
            or item["decoded_width"] != item["width"]
            or item["decoded_height"] != item["height"]
        ):
            raise CutoverError("quality generation decoded dimensions did not advance together")
        longest = max(item["width"], item["height"])
        if longest != int(item["tier"]):
            raise CutoverError("quality dimensions did not equal the requested tier")
        longest_dimensions.append(longest)
    if len(resource_ids) != 1 or len(peer_ids) != 1:
        raise CutoverError("quality ladder replaced the WHEP resource or peer")
    if any(after <= before for before, after in zip(generations, generations[1:])):
        raise CutoverError("quality ladder generation did not advance")
    if not (
        all(
            after > before
            for before, after in zip(longest_dimensions[:3], longest_dimensions[1:4])
        )
        and longest_dimensions[-1] == longest_dimensions[0]
    ):
        raise CutoverError("quality decoded/health dimensions did not follow the requested ladder")


def _validate_recovery(scrcpy: Any, engine: Any) -> None:
    scrcpy = _require_fields(
        scrcpy,
        {
            "before_generation", "after_generation", "before_engine_pid", "after_engine_pid",
            "before_whep_port", "after_whep_port", "before_peer_id", "after_peer_id", "video_resumed",
        },
        "scrcpy recovery",
    )
    if not (
        scrcpy["after_generation"] > scrcpy["before_generation"]
        and scrcpy["before_engine_pid"] == scrcpy["after_engine_pid"]
        and scrcpy["before_whep_port"] == scrcpy["after_whep_port"]
        and scrcpy["before_peer_id"] == scrcpy["after_peer_id"]
        and scrcpy["video_resumed"]
    ):
        raise CutoverError("scrcpy recovery replaced engine/WHEP/peer or failed to resume")
    engine = _require_fields(
        engine,
        {
            "before_engine_pid", "after_engine_pid", "before_whep_url", "after_whep_url",
            "before_whep_token", "after_whep_token", "fresh_select", "client_reconnected",
        },
        "engine recovery",
    )
    if not (
        engine["before_engine_pid"] != engine["after_engine_pid"]
        and engine["before_whep_url"] != engine["after_whep_url"]
        and engine["before_whep_token"] != engine["after_whep_token"]
        and engine["fresh_select"]
        and engine["client_reconnected"]
    ):
        raise CutoverError("engine recovery did not publish fresh dynamic selection and reconnect")


def _validate_soak(value: Any, config: CutoverConfig) -> str:
    soak = _require_fields(
        value,
        {"status", "elapsed_seconds", "sample_interval_seconds", "samples"},
        "soak",
    )
    if soak["status"] == "INCOMPLETE":
        return "INCOMPLETE"
    if soak["status"] != "PASS":
        raise CutoverError("soak returned an invalid outcome")
    if soak["elapsed_seconds"] < _REQUIRED_SOAK_HOURS * 3600:
        return "INCOMPLETE"
    if soak["sample_interval_seconds"] != 60:
        raise CutoverError("soak samples were not recorded every minute")
    samples = soak["samples"]
    if not isinstance(samples, list) or len(samples) != _REQUIRED_SOAK_HOURS * 60:
        raise CutoverError("soak did not contain exactly 480 minute samples")
    previous_frames = -1
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict) or not _SOAK_FIELDS.issubset(sample):
            raise CutoverError("soak sample omitted process/peer/forward/CPU/RSS/decode fields")
        numeric = [sample[field] for field in _SOAK_FIELDS]
        if any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) or item < 0 for item in numeric):
            raise CutoverError("soak sample contains invalid metrics")
        if sample["process_count"] > 6 or sample["peer_count"] > 5 or sample["forward_count"] != 5:
            raise CutoverError("soak process/peer/forward counts are unbounded")
        expected_timestamp = index * soak["sample_interval_seconds"]
        if abs(sample["sampled_at_seconds"] - expected_timestamp) > 2:
            raise CutoverError("soak samples did not follow absolute minute deadlines")
        if previous_frames >= 0 and sample["frames_decoded"] <= previous_frames:
            raise CutoverError("soak client decode stats did not advance each minute")
        previous_frames = sample["frames_decoded"]
    return "PASS"


def run_cutover_verification(config: CutoverConfig, deps: Any) -> CutoverResult:
    """Run the full direct-cutover matrix and persist bounded partial evidence."""
    result = CutoverResult()
    _validate_serials(config.serials)
    platform_name = deps.platform_name()
    if config.enforce_windows and platform_name != "Windows":
        raise CutoverError("Windows Host PC required; device behavior is not verified here")
    first_devices = _validate_ready_devices(config.serials, deps.ready_devices())
    if list(deps.preexisting_processes()):
        raise CutoverError("pre-existing app/engine/browser process found; refuse unowned attachment")
    validate_environment = getattr(deps, "validate_required_environment", None)
    if validate_environment is not None:
        validate_environment()
    _validate_performance(config, result)
    _audit_surface(config, deps)
    second_snapshot = tuple(deps.ready_devices())
    if second_snapshot != first_devices:
        raise CutoverError("ready ADB devices changed before mutation")
    second_devices = _validate_ready_devices(config.serials, second_snapshot)

    config.evidence_dir.mkdir(parents=True, exist_ok=False)
    activate_evidence = getattr(deps, "activate_evidence", None)
    if activate_evidence is not None:
        activate_evidence()
    environment = _sanitized_environment(config)
    app: Any = None
    browsers: list[Any] = []
    current_gate = "startup"
    serial = config.serials[0]
    selection: dict[str, Any] | None = None
    try:
        app = deps.start_app(environment)
        if not isinstance(app, OwnedProcess) or app.kind != "app" or app.pid <= 0 or app.started_at <= 0:
            raise CutoverError("app adapter did not return an exact owned PID/start-time handle")
        if not deps.wait_for_app(app):
            raise CutoverError("owned WindowControl app did not become ready")
        deps.register_owned_engines(config.serials)
        selection = _validate_selection(deps.select(serial), serial)

        current_gate = "local browser"
        first, first_observation = deps.open_local_browser(selection, "local-1")
        browsers.append(first)
        _browser_observation(first_observation, "first local browser")
        _health(deps.engine_health(serial), 1, False)
        second, second_observation = deps.open_local_browser(selection, "local-2")
        browsers.append(second)
        _browser_observation(second_observation, "second local browser")
        _health(deps.engine_health(serial), 2, False)
        closed = _require_fields(
            deps.close_local_session(first), {"delete_observed"}, "first local close"
        )
        if closed["delete_observed"] is not True:
            raise CutoverError("first local close did not observe the WHEP DELETE")
        _wait_for_health(
            deps, serial, 1, False, config.handshake_timeout_seconds
        )
        deps.close_browser(first)
        browsers.remove(first)
        closed = _require_fields(
            deps.close_local_session(second), {"delete_observed"}, "second local close"
        )
        if closed["delete_observed"] is not True:
            raise CutoverError("second local close did not observe the WHEP DELETE")
        _wait_for_health(
            deps, serial, 0, False, config.handshake_timeout_seconds
        )
        deps.close_browser(second)
        browsers.remove(second)
        if not _manual_gate(
            deps,
            result,
            current_gate,
            "Confirm both production local pages showed changing video, an open input DataChannel, drag, and proportional scroll before each was closed.",
        ):
            return result

        current_gate = "public browser"
        if selection["signaling_url"] != config.public_signaling_url:
            raise CutoverError("selection signaling URL does not match the configured public VPS")
        public_browser, public = deps.open_public_browser(
            selection, config.public_signaling_url
        )
        browsers.append(public_browser)
        public = _require_fields(public, {"video", "data_channel", "input"}, "public browser")
        _validate_public_websocket(
            deps.observed_public_websocket(public_browser), selection, config.public_signaling_url
        )
        if not public["video"] or not public["data_channel"] or not public["input"]:
            raise CutoverError("public browser did not use exact viewer query auth/video/input")
        if not _manual_gate(
            deps,
            result,
            current_gate,
            "Confirm the production public UI used the configured VPS, exact viewer token query auth, changing video, and DataChannel input.",
        ):
            return result
        deps.close_browser(public_browser)
        browsers.remove(public_browser)

        current_gate = "mobile"
        mobile = _require_fields(
            deps.verify_mobile(selection),
            {"bearer_auth_enabled", "whep_authenticated", "video", "input"},
            "mobile",
        )
        if not all(mobile.values()):
            raise CutoverError("mobile bearer/WHEP/video/input confirmation incomplete")
        if not _manual_gate(
            deps,
            result,
            current_gate,
            "Confirm a real mobile device used bearer auth, authenticated WHEP, decoded video, and delivered input.",
        ):
            return result

        current_gate = "local/public race"
        race = _require_fields(
            deps.race_local_public(selection, config.handshake_timeout_seconds),
            {"winner", "local_peers", "public_peer", "loser_reaped"},
            "local/public race",
        )
        valid_winner = (
            race["winner"] == "local" and race["local_peers"] == 1 and race["public_peer"] is False
        ) or (
            race["winner"] == "public" and race["local_peers"] == 0 and race["public_peer"] is True
        )
        if not valid_winner or not race["loser_reaped"]:
            raise CutoverError("local/public race did not reap loser to one winner")
        result.mark(current_gate, "PASS", f"{race['winner']} winner; loser reaped")

        current_gate = "rapid switches"
        _validate_switches(
            deps.rapid_switches(config.serials, 20, config.handshake_timeout_seconds),
            config.handshake_timeout_seconds,
        )
        result.mark(current_gate, "PASS", "20 abandoned peers reaped within timeout")

        current_gate = "quality ladder"
        _validate_quality(deps.transition_quality(serial, _REQUIRED_TIERS))
        result.mark(current_gate, "PASS", "same resource/peer; generation and dimensions advanced")

        current_gate = "scrcpy recovery"
        scrcpy = deps.recover_scrcpy(serial)
        engine = None
        _validate_recovery(scrcpy, {
            "before_engine_pid": 1, "after_engine_pid": 2,
            "before_whep_url": "before", "after_whep_url": "after",
            "before_whep_token": "before", "after_whep_token": "after",
            "fresh_select": True, "client_reconnected": True,
        })
        result.mark(current_gate, "PASS", "generation advanced on same engine/WHEP/peer")

        current_gate = "engine recovery"
        engine = deps.recover_engine(serial)
        _validate_recovery(scrcpy, engine)
        result.mark(current_gate, "PASS", "fresh dynamic selection and client reconnect")

        current_gate = "soak"
        if config.soak_hours < _REQUIRED_SOAK_HOURS:
            result.mark(current_gate, "INCOMPLETE", "shortened soak; eight hours required")
        else:
            soak = deps.run_soak(config.serials, config.soak_hours, config.sample_interval_seconds)
            soak_status = _validate_soak(soak, config)
            if soak_status == "INCOMPLETE":
                result.mark(current_gate, "INCOMPLETE", "actual soak ended before eight hours")
            else:
                result.mark(current_gate, "PASS", "eight-hour minute-sampled five-instance soak")

        current_gate = "installer"
        installer = _require_fields(
            deps.verify_installer(config.installer_path),
            {
                "installed", "launched_installed_executable", "firewall_program_rule",
                "firewall_path_matches_engine", "uninstalled", "cleanup_verified",
            },
            "installer",
        )
        if not all(installer.values()):
            raise CutoverError("installer/firewall/uninstall cleanup verification incomplete")
        result.mark(current_gate, "PASS", "installed runtime/firewall path and uninstall cleanup")

        current_gate = "tray exit"
        if not _manual_gate(
            deps,
            result,
            current_gate,
            "Confirm both source and installed WindowControl copies were exited only through their tray controls, and the app, every owned engine, and all five instance forwards are now gone.",
        ):
            return result
        exit_state = _require_fields(
            deps.tray_exit(app),
            {"tray_confirmed", "app_processes", "owned_engine_processes", "instance_forwards"},
            "tray exit",
        )
        if not exit_state["tray_confirmed"] or any(
            exit_state[field] != 0
            for field in ("app_processes", "owned_engine_processes", "instance_forwards")
        ):
            raise CutoverError("tray Exit left app/engine/forward state")
        result.mark(current_gate, "PASS", "tray-only exit left zero app/engine/forwards")

        evidence_text = str(getattr(deps, "evidence_text", ""))
        secrets_to_check = [selection["whep_token"], selection["signaling_token"]]
        if any(secret and secret in evidence_text for secret in secrets_to_check):
            raise CutoverError("secret hygiene gate detected WHEP/viewer token in logs/evidence")
        return result
    except Exception as error:
        result.mark(current_gate, "FAIL", _safe_detail(error))
        result.summary["failed_gate"] = current_gate
        result.summary["error"] = _safe_detail(error)
        return result
    finally:
        for browser in list(browsers):
            try:
                deps.close_browser(browser)
            except Exception as error:
                deps.record_event(f"browser cleanup unavailable: {_safe_detail(error)}")
        cleanup_prompts = getattr(deps, "cleanup_prompts", None)
        if cleanup_prompts is not None:
            try:
                cleanup_prompts()
            except Exception as error:
                deps.record_event(f"prompt cleanup unavailable: {_safe_detail(error)}")
        if result.status in {"FAIL", "INCOMPLETE"}:
            if config.keep_on_failure:
                result.summary["retained_on_failure"] = {
                    "keep_on_failure": True,
                    "owned_app_pid": app.pid if isinstance(app, OwnedProcess) else None,
                    "app_not_force_killed": True,
                }
            else:
                stopped = 0
                cleanup_error = None
                try:
                    stopped = int(deps.cleanup_owned_helpers())
                except Exception as error:
                    cleanup_error = _safe_detail(error)
                    deps.record_event(f"owned helper cleanup unavailable: {cleanup_error}")
                result.summary["cleanup_on_failure"] = {
                    "keep_on_failure": False,
                    "exact_owned_helpers_stopped": stopped,
                }
                if cleanup_error is not None:
                    result.summary["cleanup_on_failure"]["error"] = cleanup_error
        _write_result(config, result)


@dataclass
class _BrowserProcess:
    name: str
    process: subprocess.Popen[Any]
    started_at: float
    profile_dir: Path
    debug_port: int
    target_id: str
    websocket_url: str
    owned_pids: list[tuple[int, float]]


class RealCutoverDeps:
    """Windows adapters. Every destructive operation resolves an owned PID first."""

    _REQUIRED_ENV = (
        "AUTH_TOKEN",
        "TUNNEL_SECRET",
        "ENGINE_SIGNALING_SECRET",
        "TURN_CREDENTIAL",
        "PUBLIC_UI_URL",
    )

    def __init__(self, config: CutoverConfig):
        self.config = config
        self.evidence_text = ""
        self._prompt_channel = (
            CutoverFilePromptChannel(
                config.evidence_dir,
                poll_seconds=config.file_prompt_poll_seconds,
                record_event=self.record_event,
            )
            if config.file_prompts
            else None
        )
        self._active_browsers: list[_BrowserProcess] = []
        self._last_selection: dict[str, Any] | None = None
        self._owned_app: OwnedProcess | None = None
        self._owned_engines: dict[str, OwnedProcess] = {}
        self._evidence_active = False
        self._pending_events: list[str] = []

    def platform_name(self) -> str:
        return platform.system()

    @staticmethod
    def monotonic() -> float:
        return time.monotonic()

    @staticmethod
    def sleep(seconds: float) -> None:
        time.sleep(seconds)

    def validate_required_environment(self) -> None:
        missing = [name for name in self._REQUIRED_ENV if not os.environ.get(name)]
        if missing:
            raise CutoverError(
                "required environment secrets/config are missing: " + ", ".join(missing)
            )
        engine = self.config.repo_root / "engine" / "build" / "Release" / "engine.exe"
        if not engine.is_file():
            raise CutoverError(f"required Release engine is missing: {engine}")

    def _run(self, command: list[str], label: str, *, timeout: float = 60) -> subprocess.CompletedProcess[str]:
        self.record_event(f"{label}: {' '.join(command)}")
        try:
            completed = subprocess.run(
                command,
                cwd=self.config.repo_root,
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise CutoverError(f"{label} could not complete: {_safe_detail(error)}") from error
        if completed.returncode:
            detail = _safe_detail(completed.stderr or completed.stdout)
            raise CutoverError(f"{label} failed with exit code {completed.returncode}: {detail}")
        return completed

    def ready_devices(self) -> list[str]:
        completed = self._run(["adb", "devices"], "ADB readiness", timeout=15)
        return [
            parts[0]
            for line in completed.stdout.splitlines()
            if len(parts := line.split()) >= 2 and parts[1] == "device"
        ]

    @staticmethod
    def _is_source_app(info: dict[str, Any], target: Path) -> bool:
        command = [str(item) for item in info.get("cmdline") or []]
        cwd = info.get("cwd")
        for argument in command[1:]:
            if argument.startswith("-"):
                continue
            path = Path(argument)
            if not path.is_absolute() and cwd:
                path = Path(cwd) / path
            try:
                return path.resolve() == target
            except OSError:
                return False
        return False

    @staticmethod
    def _is_forbidden_preexisting_name(name: str) -> bool:
        return name.casefold() in {"engine.exe", "windowcontrol.exe"}

    def preexisting_processes(self) -> list[dict[str, Any]]:
        import psutil

        target = (self.config.repo_root / "src" / "main.py").resolve()
        found = []
        for process in psutil.process_iter(["pid", "name", "cmdline", "cwd"]):
            try:
                info = process.info
                name = (info.get("name") or "").casefold()
                command = " ".join(str(item) for item in info.get("cmdline") or [])
                if (
                    self._is_forbidden_preexisting_name(name)
                    or self._is_source_app(info, target)
                    or "windowcontrol-cutover-browser-" in command.casefold()
                ):
                    found.append({"pid": process.pid, "name": name})
            except (psutil.Error, OSError):
                continue
        return found

    def audit_cutover_surface(self, repo_root: Path, serial: str) -> dict[str, Any]:
        import tomllib
        import psutil

        source_root = str((repo_root / "src").resolve())
        if source_root not in sys.path:
            sys.path.insert(0, source_root)
        from server.app import create_app

        class AuditManager:
            active = None

            def list_instances(self):
                return []

        app = create_app(AuditManager())
        routes = {getattr(route, "path", "") for route in app.routes}
        forbidden_paths = {
            "/input",
            "/instances/{instance_id}/engine-select",
            "/active/whep",
            "/stream",
            "/stats",
            "/reconnect",
        }
        project = tomllib.loads((repo_root / "pyproject.toml").read_text(encoding="utf-8"))
        dependencies = project.get("project", {}).get("dependencies", [])
        forbidden_dependencies = ("aiortc", "av", "mss", "opencv", "imageio-ffmpeg")
        legacy_dependencies = [
            item for item in dependencies
            if any(item.casefold().startswith(name) for name in forbidden_dependencies)
        ]
        asset_candidates = (
            repo_root / "src" / "assets" / "mediamtx",
            repo_root / "src" / "assets" / "ffmpeg",
        )
        legacy_assets = [str(path.relative_to(repo_root)) for path in asset_candidates if path.exists()]
        legacy_processes = []
        for process in psutil.process_iter(["name"]):
            try:
                name = (process.info.get("name") or "").casefold()
            except psutil.Error:
                continue
            if name in {"mediamtx.exe", "ffmpeg.exe"}:
                legacy_processes.append(name)
        return {
            "selection_path": f"/instances/{serial}/select",
            "forbidden_routes": sorted(routes & forbidden_paths),
            "legacy_processes": legacy_processes,
            "legacy_dependencies": legacy_dependencies,
            "legacy_assets": legacy_assets,
        }

    def start_app(self, environment: dict[str, str]) -> OwnedProcess:
        command = ["uv", "run", "python", "src/main.py"]
        self.record_event("starting owned WindowControl source app")
        process = subprocess.Popen(
            command,
            cwd=self.config.repo_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        import psutil

        started_at = psutil.Process(process.pid).create_time()
        self._owned_app = OwnedProcess("app", process.pid, started_at)
        return self._owned_app

    def wait_for_app(self, app: OwnedProcess) -> bool:
        deadline = time.monotonic() + 90
        headers = self._auth_headers()
        while time.monotonic() < deadline:
            if _pid_started_at(app.pid) != app.started_at:
                return False
            try:
                response = httpx.get(
                    f"http://127.0.0.1:{self.config.app_port}/instances",
                    headers=headers,
                    timeout=2,
                )
                if response.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        return False

    @staticmethod
    def _auth_headers() -> dict[str, str]:
        return {"Authorization": f"Bearer {os.environ['AUTH_TOKEN']}"}

    def select(self, serial: str) -> dict[str, Any]:
        path = f"/instances/{quote(serial, safe='')}/select"
        response = httpx.post(
            f"http://127.0.0.1:{self.config.app_port}{path}",
            headers=self._auth_headers(),
            timeout=20,
        )
        if response.status_code != 200:
            raise CutoverError(f"production selection returned HTTP {response.status_code}")
        response_value = _require_exact_selection_response(response.json())
        self._last_selection = dict(response_value)
        value = dict(response_value)
        value["request_path"] = f"/instances/{serial}/select"
        self.record_event(
            f"production select serial={serial} generation={value.get('generation')}"
        )
        return value

    @staticmethod
    def _find_browser() -> Path:
        candidates = [
            Path(os.environ.get("PROGRAMFILES(X86)", "")) / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("PROGRAMFILES", "")) / "Microsoft/Edge/Application/msedge.exe",
            Path(os.environ.get("PROGRAMFILES", "")) / "Google/Chrome/Application/chrome.exe",
        ]
        for path in candidates:
            if path.is_file():
                return path
        raise CutoverError("Microsoft Edge or Chrome is required for owned browser helpers")

    @staticmethod
    def _free_port() -> int:
        import socket

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    def _start_browser(self, name: str, url: str, *, local_only: bool = False) -> _BrowserProcess:
        import psutil

        browser = self._find_browser()
        profile = Path(tempfile.mkdtemp(prefix=f"windowcontrol-cutover-browser-{name}-"))
        debug_port = self._free_port()
        process = subprocess.Popen(
            [
                str(browser),
                f"--user-data-dir={profile}",
                f"--remote-debugging-port={debug_port}",
                "--remote-allow-origins=*",
                "--no-first-run",
                "--new-window",
                "about:blank",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        started_at = psutil.Process(process.pid).create_time()
        deadline = time.monotonic() + 20
        target = None
        while time.monotonic() < deadline:
            try:
                pages = httpx.get(f"http://127.0.0.1:{debug_port}/json/list", timeout=1).json()
                target = next((item for item in pages if item.get("type") == "page"), None)
                if target and target.get("webSocketDebuggerUrl"):
                    break
            except (httpx.HTTPError, ValueError):
                pass
            time.sleep(0.2)
        if not target or not target.get("webSocketDebuggerUrl"):
            process.terminate()
            shutil.rmtree(profile, ignore_errors=True)
            raise CutoverError("owned browser remote-debugging endpoint did not start")
        handle = _BrowserProcess(
            name=name,
            process=process,
            started_at=started_at,
            profile_dir=profile,
            debug_port=debug_port,
            target_id=str(target["id"]),
            websocket_url=str(target["webSocketDebuggerUrl"]),
            owned_pids=[],
        )
        tracker = """
(() => {
  const Native = window.RTCPeerConnection;
  window.__wcCutoverPeers = [];
  window.__wcCutoverResources = [];
  window.__wcCutoverDeletes = [];
  window.__wcCutoverWebSockets = [];
  window.RTCPeerConnection = function(...args) {
    const pc = new Native(...args);
    pc.__wcCutoverId = crypto.randomUUID();
    window.__wcCutoverPeers.push(pc);
    return pc;
  };
  window.RTCPeerConnection.prototype = Native.prototype;

  const NativeWebSocket = window.WebSocket;
  window.WebSocket = function(url, protocols) {
    window.__wcCutoverWebSockets.push(String(url));
    return protocols === undefined
      ? new NativeWebSocket(url)
      : new NativeWebSocket(url, protocols);
  };
  window.WebSocket.prototype = NativeWebSocket.prototype;
  for (const key of ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED']) {
    Object.defineProperty(window.WebSocket, key, { value: NativeWebSocket[key] });
  }

  const nativeFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const input = args[0];
    const init = args[1] || {};
    const requestUrl = typeof input === 'string' ? input : input.url;
    const method = String(init.method || (typeof input === 'object' && input.method) || 'GET').toUpperCase();
    const response = await nativeFetch(...args);
    if (method === 'POST' && /\\/whep(?:[?#]|$)/.test(requestUrl) && response.ok) {
      const resourceLocation = response.headers && response.headers.get('Location');
      if (resourceLocation) {
        window.__wcCutoverResources.push(new URL(resourceLocation, requestUrl).href);
      }
    }
    if (method === 'DELETE' && /\\/whep\\//.test(requestUrl)) {
      window.__wcCutoverDeletes.push({
        url: new URL(requestUrl, window.location.href).href,
        ok: response.ok,
      });
    }
    LOCAL_ONLY_RESPONSE
    return response;
  };
})();
"""
        local_response = """
    if (/\\/instances\\/[^/]+\\/select(?:[?#]|$)/.test(requestUrl) && response.ok) {
      const selection = await response.clone().json();
      selection.signaling_url = null;
      selection.signaling_token = null;
      return new Response(JSON.stringify(selection), {
        status: response.status,
        statusText: response.statusText,
        headers: response.headers,
      });
    }
""" if local_only else ""
        tracker = tracker.replace("    LOCAL_ONLY_RESPONSE", local_response)
        self._cdp_session(
            handle,
            [
                ("Page.enable", {}),
                ("Page.addScriptToEvaluateOnNewDocument", {"source": tracker}),
                ("Page.navigate", {"url": url}),
            ],
        )
        time.sleep(1)
        try:
            root = psutil.Process(process.pid)
            handle.owned_pids = [
                (item.pid, item.create_time()) for item in [root, *root.children(recursive=True)]
            ]
        except psutil.Error:
            handle.owned_pids = [(process.pid, started_at)]
        return handle

    @staticmethod
    def _cdp(handle: _BrowserProcess, method: str, params: dict[str, Any]) -> dict[str, Any]:
        from websockets.sync.client import connect

        with connect(handle.websocket_url, open_timeout=5, close_timeout=2) as socket:
            return RealCutoverDeps._cdp_call(socket, method, params)

    @staticmethod
    def _cdp_call(socket: Any, method: str, params: dict[str, Any]) -> dict[str, Any]:
        request_id = secrets.randbelow(2**31 - 1) + 1
        socket.send(json.dumps({"id": request_id, "method": method, "params": params}))
        while True:
            response = json.loads(socket.recv(timeout=10))
            if response.get("id") == request_id:
                if "error" in response:
                    raise CutoverError(f"browser CDP {method} failed: {_safe_detail(response['error'])}")
                return response.get("result", {})

    @staticmethod
    def _cdp_session(handle: _BrowserProcess, calls: list[tuple[str, dict[str, Any]]]) -> None:
        """Run several CDP calls over one WebSocket connection.

        addScriptToEvaluateOnNewDocument registers against the debugging
        session that created it, so it must share a connection with the
        Page.navigate that follows it, or the registration is dropped
        before the navigated document ever loads.
        """
        from websockets.sync.client import connect

        with connect(handle.websocket_url, open_timeout=5, close_timeout=2) as socket:
            for method, params in calls:
                RealCutoverDeps._cdp_call(socket, method, params)

    def _decode_stats(self, handle: _BrowserProcess) -> dict[str, Any]:
        expression = """
(async () => {
  let frames = 0, width = 0, height = 0, peers = 0, peer_ids = [];
  for (const pc of (window.__wcCutoverPeers || [])) {
    if (pc.connectionState === 'closed') continue;
    peers += 1;
    peer_ids.push(pc.__wcCutoverId);
    const stats = await pc.getStats();
    stats.forEach(report => {
      if (report.type === 'inbound-rtp' && report.kind === 'video') {
        frames += report.framesDecoded || 0;
        width = Math.max(width, report.frameWidth || 0);
        height = Math.max(height, report.frameHeight || 0);
      }
    });
  }
  return {frames_decoded: frames, width, height, peers, peer_ids};
})()
"""
        result = self._cdp(
            handle,
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
        )
        value = result.get("result", {}).get("value")
        return _require_fields(
            value,
            {"frames_decoded", "width", "height", "peers", "peer_ids"},
            "browser decode stats",
        )

    @staticmethod
    def _one_peer_id(stats: dict[str, Any]) -> str:
        peer_ids = stats.get("peer_ids")
        if (
            not isinstance(peer_ids, list)
            or len(peer_ids) != 1
            or not isinstance(peer_ids[0], str)
            or not peer_ids[0]
        ):
            raise CutoverError("browser stats require exactly one active peer identity")
        return peer_ids[0]

    def _browser_transport_observation(self, handle: _BrowserProcess) -> dict[str, Any]:
        expression = """
(() => ({
  resource_ids: [...new Set(window.__wcCutoverResources || [])],
  delete_urls: [...new Set((window.__wcCutoverDeletes || [])
    .filter(item => item && item.ok).map(item => item.url))],
  websocket_urls: [...(window.__wcCutoverWebSockets || [])],
}))()
"""
        result = self._cdp(
            handle,
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True},
        )
        value = _require_fields(
            result.get("result", {}).get("value"),
            {"resource_ids", "delete_urls", "websocket_urls"},
            "browser transport observation",
        )
        for field in ("resource_ids", "delete_urls", "websocket_urls"):
            if not isinstance(value[field], list) or not all(
                isinstance(item, str) and item for item in value[field]
            ):
                raise CutoverError("browser transport observation contained invalid identities")
        return value

    def _active_resource_id(self, handle: _BrowserProcess) -> str:
        observed = self._browser_transport_observation(handle)
        deleted = set(observed["delete_urls"])
        resources = [
            resource for resource in observed["resource_ids"] if resource not in deleted
        ]
        if len(resources) != 1:
            raise CutoverError("browser did not expose exactly one WHEP resource Location")
        return resources[0]

    def close_local_session(self, handle: _BrowserProcess) -> dict[str, bool]:
        resource_id = self._active_resource_id(handle)
        expression = """
(async () => {
  if (typeof closeEngineInstance !== 'function') return false;
  await closeEngineInstance();
  return true;
})()
"""
        result = self._cdp(
            handle,
            "Runtime.evaluate",
            {"expression": expression, "awaitPromise": True, "returnByValue": True},
        )
        invoked = result.get("result", {}).get("value") is True
        deletes = self._browser_transport_observation(handle)["delete_urls"]
        return {"delete_observed": invoked and resource_id in deletes}

    def observed_public_websocket(self, handle: _BrowserProcess) -> str:
        deadline = self.monotonic() + self.config.handshake_timeout_seconds
        while True:
            urls = self._browser_transport_observation(handle)["websocket_urls"]
            candidates = []
            for url in urls:
                query = dict(parse_qsl(urlsplit(url).query, keep_blank_values=True))
                if query.get("role") == "viewer" and {"session", "token"}.issubset(query):
                    candidates.append(url)
            candidates = list(dict.fromkeys(candidates))
            if candidates:
                if len(candidates) != 1:
                    raise CutoverError("public browser opened multiple signaling requests")
                return candidates[0]
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise CutoverError("public browser signaling request was not observed")
            self.sleep(min(0.25, remaining))

    def open_local_browser(self, _selection: dict[str, Any], name: str):
        handle = self._start_browser(
            name,
            f"http://127.0.0.1:{self.config.app_port}/",
            local_only=True,
        )
        response = self.confirm(
            f"{name} readiness",
            f"In {name}, select {self.config.serials[0]} and perform one drag and a clearly proportional scroll; confirm changing video and an open DataChannel.",
        )
        stats = self._decode_stats(handle) if response == "PASS" else {"frames_decoded": 0}
        return handle, {
            "video": response == "PASS" and stats["frames_decoded"] > 0,
            "data_channel": response == "PASS",
            "drag": response == "PASS",
            "scroll_delta": 1 if response == "PASS" else 0,
        }

    def open_public_browser(self, _selection: dict[str, Any], public_url: str):
        if public_url != self.config.public_signaling_url:
            raise CutoverError("public browser did not receive configured signaling VPS")
        handle = self._start_browser("public", os.environ["PUBLIC_UI_URL"])
        response = self.confirm(
            "public browser readiness",
            "Log in through the production public UI, select the first instance, verify changing video/input, and inspect signaling to confirm exact viewer token query auth.",
        )
        stats = self._decode_stats(handle) if response == "PASS" else {"frames_decoded": 0}
        return handle, {
            "video": response == "PASS" and stats["frames_decoded"] > 0,
            "data_channel": response == "PASS",
            "input": response == "PASS",
        }

    def close_browser(self, handle: _BrowserProcess) -> None:
        import psutil

        for pid, started_at in reversed(handle.owned_pids):
            if _pid_started_at(pid) != started_at:
                continue
            try:
                psutil.Process(pid).terminate()
            except psutil.Error:
                pass
        handle.process.poll()
        shutil.rmtree(handle.profile_dir, ignore_errors=True)
        if handle in self._active_browsers:
            self._active_browsers.remove(handle)

    @staticmethod
    def _engine_instance_name(serial: str) -> str:
        if serial.startswith("emulator-"):
            return f"instance{(int(serial.split('-', 1)[1]) - 5554) // 2}"
        return "instance_" + serial.replace(":", "_")

    def _find_engine_candidates(self, serial: str) -> list[Any]:
        import psutil

        instance_name = self._engine_instance_name(serial)
        matches = []
        for process in psutil.process_iter(["name", "cmdline"]):
            try:
                if (process.info.get("name") or "").casefold() != "engine.exe":
                    continue
                command = [str(item) for item in process.info.get("cmdline") or []]
                if instance_name in command[1:]:
                    matches.append(process)
            except psutil.Error:
                continue
        return matches

    def _app_descendant_pids(self) -> set[int]:
        import psutil

        owned = self._owned_app
        if owned is None or _pid_started_at(owned.pid) != owned.started_at:
            raise CutoverError("owned app identity disappeared before engine registration")
        try:
            root = psutil.Process(owned.pid)
            return {process.pid for process in root.children(recursive=True)}
        except psutil.Error as error:
            raise CutoverError("could not resolve the owned app process tree") from error

    def register_owned_engines(self, serials: tuple[str, ...]) -> None:
        pending = set(serials)
        deadline = self.monotonic() + 90
        while pending:
            for serial in list(pending):
                descendants = self._app_descendant_pids()
                matches = [
                    process
                    for process in self._find_engine_candidates(serial)
                    if process.pid in descendants
                ]
                if len(matches) > 1:
                    raise CutoverError(
                        f"could not register exact app-spawned engine for {serial}"
                    )
                if len(matches) == 1:
                    process = matches[0]
                    try:
                        started_at = process.create_time()
                    except Exception as error:
                        raise CutoverError(
                            f"could not register engine identity for {serial}: {_safe_detail(error)}"
                        ) from error
                    self._owned_engines[serial] = OwnedProcess(
                        "engine", process.pid, started_at
                    )
                    pending.remove(serial)
            if not pending:
                return
            remaining = deadline - self.monotonic()
            if remaining <= 0:
                raise CutoverError("app-spawned engine registration timed out")
            self.sleep(min(0.25, remaining))

    def _engine_process(self, serial: str):
        import psutil

        owned = self._owned_engines.get(serial)
        if owned is None:
            raise CutoverError(f"engine identity was not registered for {serial}")
        current_started_at = _pid_started_at(owned.pid)
        if current_started_at is None:
            raise CutoverError(f"registered engine disappeared for {serial}")
        if current_started_at != owned.started_at:
            raise CutoverError(f"registered engine PID was replaced for {serial}")
        try:
            process = psutil.Process(owned.pid)
            name = process.name()
            command = [str(item) for item in process.cmdline()]
        except psutil.Error as error:
            raise CutoverError(f"registered engine disappeared for {serial}") from error
        if (
            name.casefold() != "engine.exe"
            or self._engine_instance_name(serial) not in command[1:]
        ):
            raise CutoverError(f"registered engine identity changed for {serial}")
        return process

    def _register_replacement_engine(
        self, serial: str, previous: OwnedProcess
    ) -> Any | None:
        matches = []
        descendants = self._app_descendant_pids()
        for process in self._find_engine_candidates(serial):
            try:
                started_at = process.create_time()
            except Exception:
                continue
            if process.pid == previous.pid or process.pid not in descendants:
                continue
            matches.append((process, started_at))
        if len(matches) > 1:
            raise CutoverError(f"engine respawn for {serial} was ambiguous")
        if not matches:
            return None
        process, started_at = matches[0]
        self._owned_engines[serial] = OwnedProcess("engine", process.pid, started_at)
        return self._engine_process(serial)

    def engine_health(self, serial: str) -> dict[str, Any]:
        process = self._engine_process(serial)
        candidates = set()
        try:
            for connection in process.net_connections(kind="inet"):
                if connection.status == "LISTEN" and connection.laddr:
                    candidates.add(int(connection.laddr.port))
        except Exception as error:
            raise CutoverError(f"could not enumerate owned engine listeners: {_safe_detail(error)}") from error
        for port in candidates:
            try:
                response = httpx.get(f"http://127.0.0.1:{port}/admin/health", timeout=1)
                value = response.json()
                if response.status_code == 200 and {"local_peers", "public_peer"}.issubset(value):
                    value["engine_pid"] = process.pid
                    value["admin_port"] = port
                    return value
            except (httpx.HTTPError, ValueError):
                continue
        raise CutoverError(f"owned engine admin health unavailable for {serial}")

    def verify_mobile(self, _selection: dict[str, Any]) -> dict[str, bool]:
        response = self.confirm(
            "mobile readiness",
            "On a real mobile device with server auth enabled, select the first instance and confirm authenticated WHEP video plus drag/scroll input.",
        )
        passed = response == "PASS"
        return {
            "bearer_auth_enabled": bool(os.environ.get("AUTH_TOKEN")) and passed,
            "whep_authenticated": passed,
            "video": passed,
            "input": passed,
        }

    def race_local_public(self, _selection: dict[str, Any], timeout: float) -> dict[str, Any]:
        handle = self._start_browser("race", f"http://127.0.0.1:{self.config.app_port}/")
        self._active_browsers.append(handle)
        response = self.confirm(
            "local/public race",
            "Select the first instance once; confirm the production client raced local/public, adopted only the first fully usable session, and the loser disappeared within the handshake timeout.",
        )
        if response != "PASS":
            return {"winner": "", "local_peers": -1, "public_peer": False, "loser_reaped": False}
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            health = self.engine_health(self.config.serials[0])
            if (health["local_peers"], health["public_peer"]) in {(1, False), (0, True)}:
                winner = "local" if health["local_peers"] == 1 else "public"
                return {"winner": winner, **{key: health[key] for key in ("local_peers", "public_peer")}, "loser_reaped": True}
            time.sleep(0.25)
        return {"winner": "", "local_peers": -1, "public_peer": False, "loser_reaped": False}

    def rapid_switches(self, serials: tuple[str, ...], count: int, timeout: float) -> list[dict[str, Any]]:
        records = []
        for index in range(count):
            abandoned = serials[index % len(serials)]
            target = serials[(index + 1) % len(serials)]
            response = self.confirm(
                f"rapid switch {index + 1}/{count}",
                f"In the retained production page, switch from {abandoned} to {target} now. Confirm the new video/DataChannel is usable.",
            )
            started = time.monotonic()
            reaped = False
            if response == "PASS":
                deadline = started + timeout
                while time.monotonic() < deadline:
                    health = self.engine_health(abandoned)
                    if health["local_peers"] == 0 and health["public_peer"] is False:
                        reaped = True
                        break
                    time.sleep(0.25)
            records.append(
                {
                    "index": index,
                    "abandoned_reaped": reaped,
                    "elapsed_seconds": time.monotonic() - started if reaped else timeout + 1,
                }
            )
            if not reaped:
                records.extend(
                    {
                        "index": remaining,
                        "abandoned_reaped": False,
                        "elapsed_seconds": timeout + 1,
                    }
                    for remaining in range(index + 1, count)
                )
                break
        return records

    def transition_quality(self, serial: str, tiers: tuple[str, ...]) -> list[dict[str, Any]]:
        if not self._active_browsers:
            raise CutoverError("quality ladder requires the retained race browser")
        handle = self._active_browsers[0]
        observations = []
        previous_generation = -1
        for tier in tiers:
            response = httpx.post(
                f"http://127.0.0.1:{self.config.app_port}/instances/{quote(serial, safe='')}/quality",
                headers=self._auth_headers(),
                json={"tier": tier},
                timeout=60,
            )
            if response.status_code != 200:
                raise CutoverError(f"quality {tier} returned HTTP {response.status_code}")
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                health = self.engine_health(serial)
                stats = self._decode_stats(handle)
                longest = max(health["width"], health["height"])
                if (
                    health["generation"] > previous_generation
                    and stats["width"] == health["width"]
                    and stats["height"] == health["height"]
                    and longest == int(tier)
                ):
                    previous_generation = health["generation"]
                    observations.append(
                        {
                            "tier": tier,
                            "resource_id": self._active_resource_id(handle),
                            "peer_id": self._one_peer_id(stats),
                            "generation": health["generation"],
                            "width": health["width"],
                            "height": health["height"],
                            "decoded_width": stats["width"],
                            "decoded_height": stats["height"],
                        }
                    )
                    break
                time.sleep(0.5)
            else:
                raise CutoverError(f"quality {tier} did not advance generation and decoded dimensions")
        return observations

    def recover_scrcpy(self, serial: str) -> dict[str, Any]:
        if not self._active_browsers:
            raise CutoverError("scrcpy recovery requires an active browser")
        before = self.engine_health(serial)
        before_stats = self._decode_stats(self._active_browsers[0])
        before_peer_id = self._one_peer_id(before_stats)
        process = self._engine_process(serial)
        whep_port = int(str(self._last_selection["whep_url"]).split(":")[-1].split("/", 1)[0])
        index = (int(serial.split("-", 1)[1]) - 5554) // 2 if serial.startswith("emulator-") else 0
        self._run(
            ["adb", "-s", serial, "shell", f"pkill -f '{self._scrcpy_kill_pattern(index)}'"],
            "selected scrcpy server kill",
            timeout=15,
        )
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            after = self.engine_health(serial)
            stats = self._decode_stats(self._active_browsers[0])
            after_peer_id = self._one_peer_id(stats)
            if after["generation"] > before["generation"] and stats["frames_decoded"] > before_stats["frames_decoded"]:
                return {
                    "before_generation": before["generation"], "after_generation": after["generation"],
                    "before_engine_pid": process.pid, "after_engine_pid": after["engine_pid"],
                    "before_whep_port": whep_port, "after_whep_port": whep_port,
                    "before_peer_id": before_peer_id,
                    "after_peer_id": after_peer_id,
                    "video_resumed": True,
                }
            time.sleep(0.5)
        raise CutoverError("scrcpy generation recovery timed out")

    @staticmethod
    def _scrcpy_kill_pattern(scid: int) -> str:
        from server.scrcpy_server import scrcpy_server_process_pattern

        return scrcpy_server_process_pattern(scid)

    def recover_engine(self, serial: str) -> dict[str, Any]:
        before_process = self._engine_process(serial)
        before_owned = self._owned_engines[serial]
        before_selection = dict(self._last_selection or self.select(serial))
        if _pid_started_at(before_owned.pid) != before_owned.started_at:
            raise CutoverError("owned engine PID was reused before recovery kill")
        before_process.terminate()
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if _pid_started_at(before_owned.pid) == before_owned.started_at:
                time.sleep(0.5)
                continue
            after_process = self._register_replacement_engine(serial, before_owned)
            if after_process is None:
                time.sleep(0.5)
                continue
            try:
                after_selection = self.select(serial)
                if after_selection["whep_url"] != before_selection["whep_url"]:
                    response = self.confirm(
                        "engine recovery reconnect",
                        "Confirm the retained production client fetched the fresh selection and resumed video/input after the owned engine respawn.",
                    )
                    return {
                        "before_engine_pid": before_process.pid,
                        "after_engine_pid": after_process.pid,
                        "before_whep_url": before_selection["whep_url"],
                        "after_whep_url": after_selection["whep_url"],
                        "before_whep_token": before_selection["whep_token"],
                        "after_whep_token": after_selection["whep_token"],
                        "fresh_select": True,
                        "client_reconnected": response == "PASS",
                    }
            except (CutoverError, httpx.HTTPError):
                pass
            time.sleep(0.5)
        raise CutoverError("engine respawn/fresh selection timed out")

    def _forward_count(self, serials: tuple[str, ...]) -> int:
        completed = self._run(["adb", "forward", "--list"], "ADB forward sample", timeout=15)
        return sum(1 for line in completed.stdout.splitlines() if line.split() and line.split()[0] in serials)

    def _runtime_process_count(self, engines: list[Any]) -> int:
        app_count = int(
            self._owned_app is not None
            and _pid_started_at(self._owned_app.pid) == self._owned_app.started_at
        )
        return app_count + len(engines)

    def run_soak(self, serials: tuple[str, ...], hours: float, interval: int) -> dict[str, Any]:
        while len(self._active_browsers) < len(serials):
            serial = serials[len(self._active_browsers)]
            handle = self._start_browser(f"soak-{len(self._active_browsers)}", f"http://127.0.0.1:{self.config.app_port}/")
            self._active_browsers.append(handle)
            if self.confirm(
                f"soak browser {serial}",
                f"In the new production browser select {serial}; confirm changing video and an open DataChannel.",
            ) != "PASS":
                raise CutoverError(f"soak browser for {serial} not ready")
        started = self.monotonic()
        duration = hours * 3600
        deadline = started + duration
        expected_samples = int(duration // interval)
        samples = []
        collection_finished = started
        for index in range(expected_samples):
            target = started + index * interval
            remaining = target - self.monotonic()
            if remaining > 0:
                self.sleep(remaining)
            sampled_at = self.monotonic()
            if sampled_at >= deadline:
                break
            engines = [self._engine_process(serial) for serial in serials]
            health = [self.engine_health(serial) for serial in serials]
            peer_count = sum(
                item["local_peers"] + int(item["public_peer"]) for item in health
            )
            decode = sum(self._decode_stats(browser)["frames_decoded"] for browser in self._active_browsers)
            cpu = sum(process.cpu_percent(interval=None) for process in engines)
            rss = sum(process.memory_info().rss for process in engines)
            samples.append(
                {
                    "sampled_at_seconds": sampled_at - started,
                    "process_count": self._runtime_process_count(engines),
                    "peer_count": peer_count,
                    "forward_count": self._forward_count(serials),
                    "cpu_percent": cpu,
                    "rss_bytes": rss,
                    "frames_decoded": decode,
                }
            )
            _write_json_atomic(
                self.config.evidence_dir / "soak-samples.json",
                {"schema_version": 1, "samples": samples},
            )
            collection_finished = self.monotonic()
        remaining = deadline - self.monotonic()
        if remaining > 0:
            self.sleep(remaining)
        elapsed = self.monotonic() - started
        complete = (
            elapsed >= duration
            and len(samples) == expected_samples
            and collection_finished <= deadline
        )
        return {
            "status": "PASS" if complete else "INCOMPLETE",
            "elapsed_seconds": elapsed,
            "sample_interval_seconds": interval,
            "samples": samples,
        }

    def verify_installer(self, installer_path: Path) -> dict[str, bool]:
        if not installer_path.is_file():
            raise CutoverError(f"produced installer is missing: {installer_path}")
        self._require_source_tray_exit_for_installer()
        self._run([str(installer_path), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], "installer install", timeout=300)
        install_root = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "WindowControl"
        executable = install_root / "WindowControl.exe"
        engine = install_root / "assets" / "engine" / "engine.exe"
        installed = executable.is_file() and engine.is_file()
        if not installed:
            raise CutoverError("installer did not stage the executable and bundled engine")
        installed_environment = dict(os.environ)
        installed_process = subprocess.Popen(
            [str(executable)],
            env=installed_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        import psutil

        installed_started_at = psutil.Process(installed_process.pid).create_time()
        time.sleep(2)
        launched = _pid_started_at(installed_process.pid) == installed_started_at
        firewall_command = [
            "powershell", "-NoProfile", "-Command",
            "$rule = Get-NetFirewallRule -DisplayName 'WindowControl-Engine' -ErrorAction SilentlyContinue; "
            "if ($null -eq $rule) { '[]' } else { $rule | Get-NetFirewallApplicationFilter | ConvertTo-Json -Compress }",
        ]
        firewall = self._run(
            firewall_command,
            "engine firewall inspection",
            timeout=60,
        ).stdout
        path_matches = self._firewall_contains_engine(firewall, engine)
        if self.confirm(
            "installed executable",
            "Confirm the installed executable starts its bundled engine and production UI. Exit that installed copy only through its tray before continuing.",
        ) != "PASS":
            raise CutoverError("installed executable was not confirmed through tray Exit")
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and _pid_started_at(installed_process.pid) == installed_started_at:
            time.sleep(0.5)
        if _pid_started_at(installed_process.pid) == installed_started_at:
            raise CutoverError("installed executable remained after its tray Exit checkpoint")
        uninstaller = install_root / "unins000.exe"
        if not uninstaller.is_file():
            raise CutoverError("installed uninstaller is missing")
        self._run([str(uninstaller), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"], "installer uninstall", timeout=300)
        firewall_after = self._run(
            firewall_command,
            "engine firewall cleanup inspection",
            timeout=60,
        ).stdout
        return {
            "installed": installed,
            "launched_installed_executable": launched,
            "firewall_program_rule": path_matches,
            "firewall_path_matches_engine": path_matches,
            "uninstalled": not executable.exists(),
            "cleanup_verified": not engine.exists() and not self._firewall_contains_engine(firewall_after, engine),
        }

    @staticmethod
    def _firewall_contains_engine(output: str, engine: Path) -> bool:
        normalized_output = output.replace("\\\\", "\\").casefold()
        return str(engine).replace("/", "\\").casefold() in normalized_output

    @staticmethod
    def _wait_owned_exit(process: OwnedProcess, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if _pid_started_at(process.pid) != process.started_at:
                return True
            time.sleep(0.5)
        return _pid_started_at(process.pid) != process.started_at

    def _require_source_tray_exit_for_installer(self) -> None:
        if self._owned_app is None:
            raise CutoverError("installer gate has no owned source app")
        if self.confirm(
            "source tray exit before installer",
            "Exit the source WindowControl app only through its tray now, and confirm its five owned engines and instance forwards are gone before installing.",
        ) != "PASS":
            raise CutoverError("source app tray Exit was not confirmed before installer")
        if not self._wait_owned_exit(self._owned_app, 60):
            raise CutoverError("owned source app remained after tray Exit before installer")

    def tray_exit(self, app: OwnedProcess) -> dict[str, Any]:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and _pid_started_at(app.pid) == app.started_at:
            time.sleep(0.5)
        engines = [item for item in self.preexisting_processes() if item["name"] == "engine.exe"]
        return {
            "tray_confirmed": _pid_started_at(app.pid) != app.started_at,
            "app_processes": 0 if _pid_started_at(app.pid) != app.started_at else 1,
            "owned_engine_processes": len(engines),
            "instance_forwards": self._forward_count(self.config.serials),
        }

    def confirm(self, checkpoint: str, message: str) -> str:
        if self._prompt_channel is not None:
            return self._prompt_channel.prompt(message, checkpoint)
        return input(f"CHECKPOINT: {checkpoint}\n{message}\nType PASS or FAIL: ").strip().upper()

    def cleanup_prompts(self) -> None:
        if self._prompt_channel is not None:
            self._prompt_channel.cleanup()
        for browser in list(self._active_browsers):
            self.close_browser(browser)

    @staticmethod
    def _stop_exact_owned_process(owned: OwnedProcess) -> bool:
        import psutil

        if _pid_started_at(owned.pid) != owned.started_at:
            return False
        try:
            process = psutil.Process(owned.pid)
            process.terminate()
            try:
                process.wait(timeout=10)
            except psutil.TimeoutExpired:
                if _pid_started_at(owned.pid) != owned.started_at:
                    return True
                process.kill()
                process.wait(timeout=5)
        except psutil.NoSuchProcess:
            return True
        except psutil.Error as error:
            raise CutoverError(
                f"could not stop exact owned {owned.kind} process: {_safe_detail(error)}"
            ) from error
        if _pid_started_at(owned.pid) == owned.started_at:
            raise CutoverError(f"exact owned {owned.kind} process did not exit")
        return True

    def cleanup_owned_helpers(self) -> int:
        stopped = 0
        seen: set[tuple[int, float]] = set()
        owned = [*reversed(tuple(self._owned_engines.values()))]
        if self._owned_app is not None:
            owned.append(self._owned_app)
        for process in owned:
            identity = (process.pid, process.started_at)
            if identity in seen:
                continue
            seen.add(identity)
            if self._stop_exact_owned_process(process):
                stopped += 1
        return stopped

    def record_event(self, message: str) -> None:
        safe = _safe_detail(message)
        self.evidence_text += "\n" + safe
        if not self._evidence_active:
            self._pending_events.append(safe)
            return
        with (self.config.evidence_dir / "verification.log").open("a", encoding="utf-8") as stream:
            stream.write(f"{time.time():.3f} {safe}\n")

    def activate_evidence(self) -> None:
        self._evidence_active = True
        for event in self._pending_events:
            with (self.config.evidence_dir / "verification.log").open("a", encoding="utf-8") as stream:
                stream.write(f"{time.time():.3f} {event}\n")
        self._pending_events.clear()


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Verify final engine direct cutover")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--serials", nargs="+")
    parser.add_argument("--performance-evidence-dir", type=Path)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--public-signaling-url")
    parser.add_argument("--installer-path", type=Path)
    parser.add_argument("--performance-override")
    parser.add_argument("--soak-hours", type=float, default=8)
    parser.add_argument("--keep-on-failure", action="store_true")
    parser.add_argument("--file-prompts", action="store_true")
    parser.add_argument("--confirm", choices=("PASS", "FAIL"))
    args = parser.parse_args(argv)
    if args.confirm:
        try:
            path = submit_file_confirmation(args.repo_root, args.confirm)
        except CutoverError as error:
            print(f"FAIL: {_safe_detail(error)}")
            return 1
        print(f"Submitted {args.confirm} confirmation to {path.parent.name}")
        return 0
    missing = [
        name
        for name in ("serials", "performance_evidence_dir", "evidence_dir", "public_signaling_url", "installer_path")
        if getattr(args, name) is None or getattr(args, name) == []
    ]
    if missing:
        parser.error("verification requires " + ", ".join("--" + name.replace("_", "-") for name in missing))
    config = CutoverConfig(
        repo_root=args.repo_root,
        serials=tuple(args.serials),
        performance_evidence_dir=args.performance_evidence_dir,
        evidence_dir=args.evidence_dir,
        public_signaling_url=args.public_signaling_url,
        installer_path=args.installer_path,
        performance_override=args.performance_override,
        soak_hours=args.soak_hours,
        keep_on_failure=args.keep_on_failure,
        file_prompts=args.file_prompts,
    )
    deps = RealCutoverDeps(config)
    try:
        result = run_cutover_verification(config, deps)
    except CutoverError as error:
        print(f"FAIL: {_safe_detail(error)}")
        return 1
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
