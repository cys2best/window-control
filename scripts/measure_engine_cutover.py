"""Repeatable five-instance legacy/engine performance comparison tool.

This module deliberately keeps the staged ``engine-select`` route confined to
the pre-deletion measurement page.  It is not part of the production client.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import secrets
import statistics
import subprocess
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote


_FAMILIES = ("WindowControl", "engine", "mediamtx", "ffmpeg")
_MANUAL_FIELDS = ("glass_to_glass_ms", "warm_switch_ms", "cold_switch_ms")
_DIAGNOSTIC_LIMIT = 240
_SENSITIVE = (
    re.compile(r'''(?i)(["']?(?:token|secret|password|authorization)["']?\s*[:=]\s*["']?(?:bearer\s+)?)\S+'''),
    re.compile(r"(?i)(authorization\s*:\s*bearer\s+)\S+"),
    re.compile(r"(?i)(bearer\s+)[^\s,;]+"),
)


class MeasurementError(RuntimeError):
    pass


@dataclass(frozen=True)
class MeasurementConfig:
    repo_root: Path
    mode: Literal["legacy", "engine"]
    workload: Literal["no-viewer", "one-viewer"]
    serials: tuple[str, ...]
    duration_seconds: int = 60
    sample_interval_seconds: float = 1.0
    evidence_dir: Path = Path("engine/test")


@dataclass(frozen=True)
class ProcessSample:
    family: str
    cpu_percent: float
    rss_bytes: int


def _safe_detail(error: BaseException | str) -> str:
    detail = " ".join(str(error).split())
    for pattern in _SENSITIVE:
        detail = pattern.sub(r"\1<redacted>", detail)
    return detail[:_DIAGNOSTIC_LIMIT] + ("..." if len(detail) > _DIAGNOSTIC_LIMIT else "")


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _result_path(evidence_dir: Path) -> Path:
    return evidence_dir / f"result-{int(time.time())}-{os.getpid()}.json"


def _validate_config(config: MeasurementConfig) -> None:
    if config.mode not in {"legacy", "engine"}:
        raise MeasurementError(f"mode must be legacy or engine, got {config.mode!r}")
    if config.workload not in {"no-viewer", "one-viewer"}:
        raise MeasurementError(f"workload must be no-viewer or one-viewer, got {config.workload!r}")
    if len(config.serials) != 5 or len(set(config.serials)) != 5 or not all(config.serials):
        raise MeasurementError("expected exactly five unique ready serials")
    if config.duration_seconds < 30:
        raise MeasurementError("duration_seconds must be at least 30")
    if config.sample_interval_seconds <= 0:
        raise MeasurementError("sample_interval_seconds must be positive")


def _empty_metrics() -> dict[str, int]:
    return {"cpu_median": 0, "cpu_p95": 0, "rss_peak": 0}


def _percentile95(values: list[float]) -> float:
    if not values:
        return 0
    ordered = sorted(values)
    index = (len(ordered) - 1) * 0.95
    lower, upper = math.floor(index), math.ceil(index)
    return ordered[lower] if lower == upper else ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _number(value: Any, field: str) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise MeasurementError(f"{field} must be numeric")
    return value


def _aggregate_processes(samples: list[list[ProcessSample]], mode: str) -> dict[str, dict[str, float | int]]:
    by_family: dict[str, list[tuple[float, int]]] = {family: [] for family in _FAMILIES}
    aggregate_cpu: list[float] = []
    aggregate_rss: list[int] = []
    for snapshot in samples:
        family_totals = {family: [0.0, 0] for family in _FAMILIES}
        for item in snapshot:
            if item.family not in by_family:
                continue
            if mode == "engine" and item.family in {"mediamtx", "ffmpeg"}:
                raise MeasurementError(f"unexpected legacy process family in engine mode: {item.family}")
            if mode == "legacy" and item.family == "engine":
                raise MeasurementError("unexpected engine process family in legacy mode")
            cpu = _number(item.cpu_percent, f"{item.family} cpu")
            rss = _number(item.rss_bytes, f"{item.family} RSS")
            family_totals[item.family][0] += cpu
            family_totals[item.family][1] += int(rss)
        for family, (cpu, rss) in family_totals.items():
            by_family[family].append((cpu, rss))
        aggregate_cpu.append(sum(cpu for cpu, _rss in family_totals.values()))
        aggregate_rss.append(sum(rss for _cpu, rss in family_totals.values()))

    def summarize(items: list[tuple[float, int]]) -> dict[str, float | int]:
        if not items:
            return _empty_metrics()
        cpus = [cpu for cpu, _rss in items]
        return {
            "cpu_median": statistics.median(cpus),
            "cpu_p95": _percentile95(cpus),
            "rss_peak": max(rss for _cpu, rss in items),
        }

    result = {family: summarize(by_family[family]) for family in _FAMILIES}
    result["aggregate"] = {
        "cpu_median": statistics.median(aggregate_cpu) if aggregate_cpu else 0,
        "cpu_p95": _percentile95(aggregate_cpu),
        "rss_peak": max(aggregate_rss, default=0),
    }
    return result


def _viewer_records(serials: tuple[str, ...], raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict) or set(raw) != set(serials):
        raise MeasurementError("one-viewer workload requires viewer metrics for every serial")
    records = []
    for serial in serials:
        value = raw[serial]
        if not isinstance(value, dict):
            raise MeasurementError(f"viewer metrics for {serial} must be an object")
        record = {"serial": serial}
        for field in ("bits_per_second", "jitter_buffer_ms", "frames_per_second"):
            record[field] = _number(value.get(field), f"viewer {field}")
        for field in ("connected_at", "switched_at"):
            timestamp = value.get(field)
            if not isinstance(timestamp, str) or not timestamp:
                raise MeasurementError(f"viewer {field} is required")
            record[field] = timestamp
        records.append(record)
    return records


def _manual_records(raw: Any) -> dict[str, float | int]:
    if not isinstance(raw, dict):
        raise MeasurementError("manual metrics must be an object with numeric values")
    return {field: _number(raw.get(field), field) for field in _MANUAL_FIELDS}


def _partial_result(config: MeasurementConfig, deps: Any, started_at: float, error: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "mode": config.mode,
        "workload": config.workload,
        "commit": _commit(deps),
        "serials": list(config.serials),
        "started_at": started_at,
        "duration_seconds": config.duration_seconds,
        "processes": {family: _empty_metrics() for family in (*_FAMILIES, "aggregate")},
        "viewer_metrics": [],
        "manual_metrics": {field: None for field in _MANUAL_FIELDS},
        "result": "FAIL",
        "diagnostic": error,
    }


def _commit(deps: Any) -> str:
    try:
        value = deps.commit()
    except Exception:
        return "unknown"
    return value if isinstance(value, str) and value else "unknown"


def run_measurement(config: MeasurementConfig, deps: Any) -> dict[str, Any]:
    """Return and persist a versioned measurement result or raise MeasurementError."""
    started_at = deps.clock()
    result_path = _result_path(config.evidence_dir)
    app = None
    viewer = None
    try:
        _validate_config(config)
        config.evidence_dir.mkdir(parents=True, exist_ok=True)
        before = tuple(deps.ready_serials())
        if before != config.serials:
            raise MeasurementError("expected exactly five unique ready serials matching the supplied serials")
        environment = dict(os.environ)
        if config.mode == "legacy":
            environment.pop("ENGINE_EXE_PATH", None)
        else:
            engine_exe = config.repo_root / "engine" / "build" / "Release" / "engine.exe"
            if not engine_exe.exists():
                raise MeasurementError(f"verified Release engine is missing: {engine_exe}")
            environment["ENGINE_EXE_PATH"] = str(engine_exe)
        app = deps.start_app(environment)
        if config.workload == "one-viewer":
            if not deps.wait_for_app_ready(app):
                raise MeasurementError("WindowControl is not ready before one-viewer measurement")
            viewer = deps.start_viewer(config)
        snapshots: list[list[ProcessSample]] = []
        deadline = deps.clock() + config.duration_seconds
        while deps.clock() < deadline:
            snapshot = deps.sample_processes()
            if isinstance(snapshot, BaseException):
                raise snapshot
            snapshots.append(list(snapshot))
            deps.sleep(min(config.sample_interval_seconds, max(0, deadline - deps.clock())))
        processes = _aggregate_processes(snapshots, config.mode)
        after = tuple(deps.ready_serials())
        if after != before:
            raise MeasurementError(f"ready serials changed during measurement: {before} -> {after}")
        if config.workload == "one-viewer":
            viewer_metrics = _viewer_records(config.serials, deps.finish_viewer(viewer))
            manual_metrics = _manual_records(deps.collect_manual_metrics())
        else:
            submitted = getattr(deps, "unexpected_no_viewer_submission", lambda: None)()
            if submitted is not None:
                raise MeasurementError("no-viewer workload received unexpected viewer/manual metrics")
            viewer_metrics = []
            manual_metrics = {field: None for field in _MANUAL_FIELDS}
        result = {
            "schema_version": 1,
            "mode": config.mode,
            "workload": config.workload,
            "commit": _commit(deps),
            "serials": list(config.serials),
            "started_at": started_at,
            "duration_seconds": config.duration_seconds,
            "processes": processes,
            "viewer_metrics": viewer_metrics,
            "manual_metrics": manual_metrics,
            "result": "PASS",
        }
        _write_json_atomic(result_path, result)
        return result
    except MeasurementError as error:
        _write_json_atomic(result_path, _partial_result(config, deps, started_at, _safe_detail(error)))
        raise
    except Exception as error:
        safe = _safe_detail(error)
        _write_json_atomic(result_path, _partial_result(config, deps, started_at, safe))
        raise MeasurementError(safe) from error
    finally:
        if app is not None:
            deps.stop_app(app)


class _MetricPageServer:
    def __init__(self, page: Path, app_origin: str, auth_token: str):
        self.page = page
        self.app_origin = app_origin.rstrip("/")
        self.auth_token = auth_token
        self.metrics: dict[str, dict[str, Any]] = {}
        self.done = threading.Event()
        parent = self

        class Handler(BaseHTTPRequestHandler):
            def _send_response(self, status: int, body: bytes = b"", *, content_type: str | None = None, cookie: str | None = None) -> None:
                self.send_response(status)
                if content_type:
                    self.send_header("Content-Type", content_type)
                if cookie:
                    self.send_header("Set-Cookie", cookie)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                if body:
                    self.wfile.write(body)
                self.close_connection = True

            def do_GET(self):
                if self.path.split("?", 1)[0] != "/cutover_metrics_page.html":
                    self.send_error(404)
                    return
                body = parent.page.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.path == "/measurement-login":
                    if not parent.auth_token:
                        self._send_response(204)
                        return
                    import httpx
                    try:
                        response = httpx.post(
                            f"{parent.app_origin}/login", json={"token": parent.auth_token}, timeout=10,
                        )
                    except httpx.HTTPError:
                        self._send_response(502)
                        return
                    self._send_response(response.status_code, cookie=response.headers.get("set-cookie"))
                    return
                if self.path.startswith("/instances/") and self.path.endswith(("/select", "/engine-select")):
                    import httpx
                    body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    headers = {"Content-Type": self.headers.get("Content-Type", "application/json")}
                    if cookie := self.headers.get("Cookie"):
                        headers["Cookie"] = cookie
                    try:
                        response = httpx.post(f"{parent.app_origin}{self.path}", content=body, headers=headers, timeout=30)
                    except httpx.HTTPError:
                        self._send_response(502)
                        return
                    self._send_response(response.status_code, response.content, content_type=response.headers.get("content-type", "application/json"))
                    return
                if self.path != "/metrics":
                    self.send_error(404)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    value = json.loads(self.rfile.read(length).decode("utf-8"))
                    serial = value["serial"]
                    if not isinstance(serial, str):
                        raise ValueError("serial")
                    parent.metrics[serial] = value
                    self._send_response(204)
                except (ValueError, KeyError, UnicodeDecodeError, json.JSONDecodeError):
                    self.send_error(400)

            def log_message(self, _format, *_args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}/cutover_metrics_page.html"

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def start(self) -> None:
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


def _pid_started_at(pid: int) -> float | None:
    import psutil
    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return None


class MetricFilePrompt:
    """Nonce-scoped numeric prompt following the Phase 2 file-prompt pattern."""
    prompt_name = "active-metrics-prompt.json"

    def __init__(self, evidence_dir: Path):
        self.evidence_dir = evidence_dir
        self.pid = os.getpid()
        self.started_at = _pid_started_at(self.pid)
        if self.started_at is None:
            raise MeasurementError("could not determine measurement process start time")

    @staticmethod
    def response_path(evidence_dir: Path, nonce: str) -> Path:
        return evidence_dir / f"metrics-response-{hashlib.sha256(nonce.encode()).hexdigest()}.json"

    def prompt(self) -> dict[str, Any]:
        nonce = secrets.token_hex(16)
        prompt = {"version": 1, "measurement_pid": self.pid, "measurement_started_at": self.started_at,
                  "nonce": nonce, "fields": list(_MANUAL_FIELDS)}
        prompt_path = self.evidence_dir / self.prompt_name
        _write_json_atomic(prompt_path, prompt)
        response_path = self.response_path(self.evidence_dir, nonce)
        print("Waiting for nonce-scoped metrics; in another terminal run "
              ".\\engine\\measure-engine-cutover.ps1 -SubmitManualMetrics 'glass_to_glass_ms,warm_switch_ms,cold_switch_ms'", flush=True)
        try:
            while True:
                if response_path.exists():
                    try:
                        response = json.loads(response_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        response = None
                    response_path.unlink(missing_ok=True)
                    if (isinstance(response, dict) and response.get("version") == 1
                            and response.get("measurement_pid") == self.pid
                            and response.get("measurement_started_at") == self.started_at
                            and response.get("nonce") == nonce and isinstance(response.get("metrics"), dict)):
                        return response["metrics"]
                time.sleep(0.25)
        finally:
            prompt_path.unlink(missing_ok=True)
            response_path.unlink(missing_ok=True)


def submit_manual_metrics(repo_root: Path, values: str) -> Path:
    try:
        metrics = dict(zip(_MANUAL_FIELDS, (float(item.strip()) for item in values.split(",")), strict=True))
    except ValueError as error:
        raise MeasurementError("manual metrics must be three comma-separated numeric values") from error
    active: list[tuple[Path, dict[str, Any]]] = []
    for prompt_path in (repo_root / "engine" / "test").glob("performance-*/active-metrics-prompt.json"):
        try:
            prompt = json.loads(prompt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (isinstance(prompt, dict) and isinstance(prompt.get("measurement_pid"), int)
                and _pid_started_at(prompt["measurement_pid"]) == prompt.get("measurement_started_at")
                and isinstance(prompt.get("nonce"), str)):
            active.append((prompt_path, prompt))
    if len(active) != 1:
        raise MeasurementError("expected exactly one live manual-metrics prompt")
    prompt_path, prompt = active[0]
    target = MetricFilePrompt.response_path(prompt_path.parent, prompt["nonce"])
    payload = {"version": 1, "measurement_pid": prompt["measurement_pid"],
               "measurement_started_at": prompt["measurement_started_at"], "nonce": prompt["nonce"], "metrics": metrics}
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        try:
            os.link(temporary, target)
        except FileExistsError as error:
            raise MeasurementError("manual metrics already submitted for this prompt") from error
    finally:
        temporary.unlink(missing_ok=True)
    return target


class DecisionFilePrompt:
    """Keeps owner approval nonce-scoped and separate from metric values."""
    prompt_name = "active-decision-prompt.json"

    @staticmethod
    def response_path(evidence_dir: Path, nonce: str) -> Path:
        return evidence_dir / f"decision-response-{hashlib.sha256(nonce.encode()).hexdigest()}.json"

    @classmethod
    def await_decision(cls, result_files: list[Path], evidence_dir: Path) -> Path:
        pid, started_at = os.getpid(), _pid_started_at(os.getpid())
        if started_at is None:
            raise MeasurementError("could not determine decision process start time")
        nonce = secrets.token_hex(16)
        prompt_path = evidence_dir / cls.prompt_name
        prompt = {"version": 1, "decision_pid": pid, "decision_started_at": started_at, "nonce": nonce,
                  "result_files": [str(path.resolve()) for path in result_files],
                  "expected_decisions": ["APPROVE CUTOVER", "OVERRIDE CUTOVER: <reason>", "REJECT CUTOVER: <reason>"]}
        _write_json_atomic(prompt_path, prompt)
        response_path = cls.response_path(evidence_dir, nonce)
        print("Waiting for owner decision. In a second terminal run "
              ".\\engine\\measure-engine-cutover.ps1 -RecordDecision 'APPROVE CUTOVER'", flush=True)
        try:
            while True:
                if response_path.exists():
                    try:
                        response = json.loads(response_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError):
                        response = None
                    response_path.unlink(missing_ok=True)
                    if (isinstance(response, dict) and response.get("version") == 1
                            and response.get("decision_pid") == pid and response.get("decision_started_at") == started_at
                            and response.get("nonce") == nonce and isinstance(response.get("decision"), str)):
                        return record_cutover_decision(result_files, response["decision"], evidence_dir)
        finally:
            prompt_path.unlink(missing_ok=True)
            response_path.unlink(missing_ok=True)


def submit_cutover_decision(repo_root: Path, decision: str) -> Path:
    active: list[tuple[Path, dict[str, Any]]] = []
    for path in (repo_root / "engine" / "test").glob("performance-*/active-decision-prompt.json"):
        try:
            prompt = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (isinstance(prompt, dict) and isinstance(prompt.get("decision_pid"), int)
                and _pid_started_at(prompt["decision_pid"]) == prompt.get("decision_started_at")
                and isinstance(prompt.get("nonce"), str)):
            active.append((path, prompt))
    if len(active) != 1:
        raise MeasurementError("expected exactly one live owner-decision prompt")
    prompt_path, prompt = active[0]
    target = DecisionFilePrompt.response_path(prompt_path.parent, prompt["nonce"])
    payload = {"version": 1, "decision_pid": prompt["decision_pid"], "decision_started_at": prompt["decision_started_at"],
               "nonce": prompt["nonce"], "decision": decision}
    temporary = target.with_name(f".{target.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_text(json.dumps(payload), encoding="utf-8")
        try:
            os.link(temporary, target)
        except FileExistsError as error:
            raise MeasurementError("owner decision already submitted for this prompt") from error
    finally:
        temporary.unlink(missing_ok=True)
    return target


class RealMeasurementDeps:
    """Windows adapters; tests use a small fake object instead."""

    def __init__(self, config: MeasurementConfig, app_port: int = 8080):
        self.config = config
        self.app_port = app_port
        self._page_server: _MetricPageServer | None = None

    def ready_serials(self) -> tuple[str, ...]:
        completed = subprocess.run(["adb", "devices"], text=True, capture_output=True, check=False)
        if completed.returncode:
            raise MeasurementError(f"adb devices failed: {_safe_detail(completed.stderr)}")
        return tuple(line.split()[0] for line in completed.stdout.splitlines() if len(line.split()) >= 2 and line.split()[1] == "device")

    def start_app(self, environment: dict[str, str]):
        self.config.evidence_dir.mkdir(parents=True, exist_ok=True)
        return subprocess.Popen(["uv", "run", "python", "src/main.py"], cwd=self.config.repo_root, env=environment, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT, text=True)

    def stop_app(self, app: Any) -> None:
        if app.poll() is None:
            app.terminate()
            try:
                app.wait(timeout=10)
            except subprocess.TimeoutExpired:
                app.kill()
        if self._page_server:
            self._page_server.close()

    def sample_processes(self) -> list[ProcessSample]:
        import psutil

        samples: list[ProcessSample] = []
        for process in psutil.process_iter(["name", "cmdline"]):
            try:
                name = (process.info.get("name") or "").casefold()
                command = " ".join(str(part) for part in (process.info.get("cmdline") or [])).casefold()
                family = None
                if name == "engine.exe": family = "engine"
                elif "mediamtx" in name or "mediamtx" in command: family = "mediamtx"
                elif "ffmpeg" in name or "ffmpeg" in command: family = "ffmpeg"
                elif "src/main.py" in command or "windowcontrol" in name: family = "WindowControl"
                if family:
                    samples.append(ProcessSample(family, process.cpu_percent(), process.memory_info().rss))
            except psutil.Error:
                continue
        return samples

    def wait_for_app_ready(self, app: Any) -> bool:
        import httpx

        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            if app.poll() is not None:
                return False
            try:
                response = httpx.get(f"http://127.0.0.1:{self.app_port}/", timeout=1)
                if response.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
        return False

    def start_viewer(self, config: MeasurementConfig) -> _MetricPageServer:
        page = _MetricPageServer(
            config.repo_root / "engine" / "test" / "cutover_metrics_page.html",
            f"http://127.0.0.1:{self.app_port}",
            os.environ.get("AUTH_TOKEN", ""),
        )
        self._page_server = page
        page.start()
        fragment = quote(json.dumps({
            "mode": config.mode,
            "serials": config.serials,
            "stable_window_seconds": config.duration_seconds,
        }))
        webbrowser.open(f"{page.url}#{fragment}")
        return page

    def finish_viewer(self, page: _MetricPageServer) -> dict[str, Any]:
        config = self.config
        deadline = time.monotonic() + config.duration_seconds * len(config.serials) + 120
        while time.monotonic() < deadline:
            if set(page.metrics) == set(config.serials):
                return page.metrics
            time.sleep(0.25)
        raise MeasurementError("timed out waiting for one-viewer metrics page")

    def collect_manual_metrics(self) -> dict[str, Any]:
        return MetricFilePrompt(self.config.evidence_dir).prompt()

    def unexpected_no_viewer_submission(self) -> None:
        # No page or prompt is started for this workload, so there is no valid
        # submission channel to observe.
        return None

    def commit(self) -> str:
        completed = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.config.repo_root, text=True, capture_output=True, check=False)
        return completed.stdout.strip() if completed.returncode == 0 else "unknown"

    def clock(self) -> float:
        return time.time()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


def _load_result(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MeasurementError(f"could not read result {path.name}: {_safe_detail(error)}") from error
    _validate_completed_result(value, path.name)
    return value


def _validate_completed_result(value: Any, label: str) -> None:
    expected_top_level = {
        "schema_version", "mode", "workload", "commit", "serials", "started_at",
        "duration_seconds", "processes", "viewer_metrics", "manual_metrics", "result",
    }
    if not isinstance(value, dict) or set(value) != expected_top_level:
        raise MeasurementError(f"result is not a complete schema-v1 measurement: {label}")
    if value["schema_version"] != 1 or value["result"] != "PASS":
        raise MeasurementError(f"result is not a complete schema-v1 measurement: {label}")
    if value["mode"] not in {"legacy", "engine"} or value["workload"] not in {"no-viewer", "one-viewer"}:
        raise MeasurementError(f"result is not a complete schema-v1 measurement: {label}")
    serials = value["serials"]
    if not isinstance(serials, list) or len(serials) != 5 or len(set(serials)) != 5 or not all(isinstance(serial, str) and serial for serial in serials):
        raise MeasurementError(f"result is not a complete schema-v1 measurement: {label}")
    if not isinstance(value["commit"], str) or not value["commit"]:
        raise MeasurementError(f"result is not a complete schema-v1 measurement: {label}")
    try:
        _number(value["started_at"], "started_at")
    except MeasurementError as error:
        raise MeasurementError(f"result is not a complete schema-v1 measurement: {label}") from error
    if type(value["duration_seconds"]) is not int or value["duration_seconds"] < 30:
        raise MeasurementError(f"result is not a complete schema-v1 measurement: {label}")
    processes = value["processes"]
    if not isinstance(processes, dict) or set(processes) != {*_FAMILIES, "aggregate"}:
        raise MeasurementError(f"result is not a complete schema-v1 measurement: {label}")
    for metrics in processes.values():
        if not isinstance(metrics, dict) or set(metrics) != {"cpu_median", "cpu_p95", "rss_peak"}:
            raise MeasurementError(f"result is not a complete schema-v1 measurement: {label}")
        try:
            if any(_number(metrics[field], field) < 0 for field in metrics):
                raise MeasurementError("negative")
        except MeasurementError as error:
            raise MeasurementError(f"result is not a complete schema-v1 measurement: {label}") from error
    manual = value["manual_metrics"]
    if not isinstance(manual, dict) or set(manual) != set(_MANUAL_FIELDS):
        raise MeasurementError(f"result is not a complete schema-v1 measurement: {label}")
    viewer = value["viewer_metrics"]
    if value["workload"] == "no-viewer":
        if viewer != [] or any(manual[field] is not None for field in _MANUAL_FIELDS):
            raise MeasurementError(f"result is not a complete schema-v1 measurement: {label}")
        return
    if not isinstance(viewer, list) or len(viewer) != 5 or {record.get("serial") for record in viewer if isinstance(record, dict)} != set(serials):
        raise MeasurementError(f"result is not a complete schema-v1 measurement: {label}")
    for record in viewer:
        if not isinstance(record, dict) or set(record) != {"serial", "bits_per_second", "jitter_buffer_ms", "frames_per_second", "connected_at", "switched_at"}:
            raise MeasurementError(f"result is not a complete schema-v1 measurement: {label}")
        try:
            if any(_number(record[field], field) < 0 for field in ("bits_per_second", "jitter_buffer_ms", "frames_per_second")):
                raise MeasurementError("negative")
        except MeasurementError as error:
            raise MeasurementError(f"result is not a complete schema-v1 measurement: {label}") from error
        if not all(isinstance(record[field], str) and record[field] for field in ("connected_at", "switched_at")):
            raise MeasurementError(f"result is not a complete schema-v1 measurement: {label}")
    try:
        if any(_number(manual[field], field) < 0 for field in _MANUAL_FIELDS):
            raise MeasurementError("negative")
    except MeasurementError as error:
        raise MeasurementError(f"result is not a complete schema-v1 measurement: {label}") from error


def record_cutover_decision(result_files: list[Path], decision: str, output_dir: Path) -> Path:
    """Write an explicit APPROVE/OVERRIDE decision; never derive it from metrics."""
    if len(result_files) != 4:
        raise MeasurementError("exactly four result files are required for a cutover decision")
    decision = decision.strip()
    if decision == "APPROVE CUTOVER":
        reason = None
        outcome = "APPROVE CUTOVER"
    elif decision.startswith("OVERRIDE CUTOVER:") and decision.split(":", 1)[1].strip():
        reason = decision.split(":", 1)[1].strip()
        outcome = "OVERRIDE CUTOVER"
    elif decision.startswith("REJECT CUTOVER:"):
        raise MeasurementError("REJECT CUTOVER writes no approval artifact")
    else:
        raise MeasurementError("decision must be APPROVE CUTOVER or OVERRIDE CUTOVER: <reason>")
    results = [_load_result(path) for path in result_files]
    keys = {(result["mode"], result["workload"]) for result in results}
    if keys != {("legacy", "no-viewer"), ("legacy", "one-viewer"), ("engine", "no-viewer"), ("engine", "one-viewer")}:
        raise MeasurementError("results must contain each legacy/engine and no-viewer/one-viewer combination exactly once")
    serial_sets = {tuple(result["serials"]) for result in results}
    if len(serial_sets) != 1:
        raise MeasurementError("the four result files do not use the same five serials")
    payload = {
        "schema_version": 1,
        "decision": outcome,
        "reason": reason,
        "decided_at": time.time(),
        "result_hashes": {str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest() for path in result_files},
    }
    target = output_dir / "cutover-decision.json"
    _write_json_atomic(target, payload)
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--mode", choices=("legacy", "engine"))
    parser.add_argument("--workload", choices=("no-viewer", "one-viewer"))
    parser.add_argument("--serial", action="append", default=[])
    parser.add_argument("--duration-seconds", type=int, default=60)
    parser.add_argument("--sample-interval-seconds", type=float, default=1.0)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--record-decision")
    parser.add_argument("--await-decision", action="store_true")
    parser.add_argument("--result-file", type=Path, action="append", default=[])
    parser.add_argument("--submit-manual-metrics")
    args = parser.parse_args()
    if args.submit_manual_metrics is not None:
        try:
            target = submit_manual_metrics(args.repo_root, args.submit_manual_metrics)
        except MeasurementError as error:
            print(f"FAIL: {error}")
            return 1
        print(f"Submitted manual metrics to {target.parent.name}")
        return 0
    if args.await_decision:
        if args.evidence_dir is None:
            parser.error("--evidence-dir is required with --await-decision")
        try:
            target = DecisionFilePrompt.await_decision(args.result_file, args.evidence_dir)
        except MeasurementError as error:
            print(f"FAIL: {error}")
            return 1
        print(f"Recorded explicit cutover decision: {target}")
        return 0
    if args.record_decision is not None:
        try:
            target = submit_cutover_decision(args.repo_root, args.record_decision)
        except MeasurementError as error:
            print(f"FAIL: {error}")
            return 1
        print(f"Submitted explicit cutover decision to {target.parent.name}")
        return 0
    if args.evidence_dir is None or args.mode is None or args.workload is None:
        parser.error("--mode, --workload, and --evidence-dir are required for measurement")
    config = MeasurementConfig(args.repo_root, args.mode, args.workload, tuple(args.serial), args.duration_seconds, args.sample_interval_seconds, args.evidence_dir)
    try:
        result = run_measurement(config, RealMeasurementDeps(config))
    except MeasurementError as error:
        print(f"FAIL: {error}")
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
