import json
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading

import httpx
import pytest

import scripts.measure_engine_cutover as measurement
from scripts.measure_engine_cutover import (
    MeasurementConfig,
    MeasurementError,
    ProcessSample,
    RealMeasurementDeps,
    _MetricPageServer,
    record_cutover_decision,
    run_measurement,
)


SERIALS = (
    "emulator-5554",
    "emulator-5556",
    "emulator-5558",
    "emulator-5560",
    "emulator-5562",
)


@dataclass
class FakeApp:
    alive: bool = True

    def poll(self):
        return None if self.alive else 1


class FakeDeps:
    def __init__(self, serials=SERIALS):
        self.serials = tuple(serials)
        self.ready_checks = [self.serials, self.serials]
        self.process_samples = [[
            ProcessSample("WindowControl", 10, 100),
            ProcessSample("engine", 20, 200),
        ]]
        self.viewer_metrics = {
            serial: {
                "bits_per_second": 4_000_000,
                "jitter_buffer_ms": 18,
                "frames_per_second": 30,
                "connected_at": "2026-09-01T00:00:00Z",
                "switched_at": "2026-09-01T00:01:00Z",
            }
            for serial in self.serials
        }
        self.manual_metrics = {
            "glass_to_glass_ms": 120,
            "warm_switch_ms": 350,
            "cold_switch_ms": 900,
        }
        self.now = 1_000.0
        self.started_env = None
        self.opened = []
        self.non_viewer_submission = None
        self.viewer_active = False
        self.events = []

    @classmethod
    def five_instances(cls):
        return cls()

    def ready_serials(self):
        return self.ready_checks.pop(0) if self.ready_checks else self.serials

    def start_app(self, environment):
        self.started_env = dict(environment)
        return FakeApp()

    def start_viewer(self, config):
        self.viewer_active = True
        self.events.append("viewer-start")
        return object()

    def finish_viewer(self, _viewer):
        self.viewer_active = False
        self.events.append("viewer-finish")
        return self.viewer_metrics

    def stop_app(self, app):
        app.alive = False

    def sample_processes(self):
        self.events.append(f"sample:{self.viewer_active}")
        return self.process_samples.pop(0) if self.process_samples else []

    def collect_viewer_metrics(self, config):
        self.opened.append(config.workload)
        return self.viewer_metrics

    def collect_manual_metrics(self):
        return self.manual_metrics

    def unexpected_no_viewer_submission(self):
        return self.non_viewer_submission

    def commit(self):
        return "a" * 40

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def make_config(tmp_path, **changes):
    engine_exe = tmp_path / "engine" / "build" / "Release" / "engine.exe"
    engine_exe.parent.mkdir(parents=True, exist_ok=True)
    engine_exe.touch()
    values = dict(
        repo_root=tmp_path,
        mode="engine",
        workload="one-viewer",
        serials=SERIALS,
        duration_seconds=60,
        sample_interval_seconds=30,
        evidence_dir=tmp_path,
    )
    values.update(changes)
    return MeasurementConfig(**values)


def test_rejects_anything_other_than_five_unique_ready_serials(tmp_path):
    """Removing five-unique validation would allow an invalid comparison."""
    deps = FakeDeps(serials=("emulator-5554",) * 5)

    with pytest.raises(MeasurementError, match="five unique"):
        run_measurement(make_config(tmp_path, serials=deps.serials), deps)


def test_aggregates_process_and_viewer_samples_into_schema_v1(tmp_path):
    """Dropping a process family or aggregate sample would corrupt evidence."""
    deps = FakeDeps.five_instances()
    deps.process_samples = [
        [ProcessSample("WindowControl", 10, 100), ProcessSample("engine", 20, 200)],
        [ProcessSample("WindowControl", 14, 110), ProcessSample("engine", 24, 210)],
    ]

    result = run_measurement(make_config(tmp_path), deps)

    assert result["schema_version"] == 1
    assert result["processes"]["aggregate"]["cpu_median"] == 34
    assert result["processes"]["mediamtx"] == {"cpu_median": 0, "cpu_p95": 0, "rss_peak": 0}
    assert len(result["viewer_metrics"]) == 5
    assert result["manual_metrics"]["glass_to_glass_ms"] == 120
    assert result["result"] == "PASS"
    assert json.loads(next(tmp_path.glob("result*.json")).read_text())["result"] == "PASS"


def test_missing_or_non_numeric_manual_metric_fails_and_preserves_partial_json(tmp_path):
    """Accepting PASS text instead of a number would defeat the metric gate."""
    deps = FakeDeps.five_instances()
    deps.manual_metrics = {"glass_to_glass_ms": "PASS"}

    with pytest.raises(MeasurementError, match="numeric"):
        run_measurement(make_config(tmp_path), deps)

    assert json.loads(next(tmp_path.glob("result*.json")).read_text())["result"] == "FAIL"


@pytest.mark.parametrize("field,value", [("mode", "both"), ("workload", "many-viewers")])
def test_rejects_an_unknown_mode_or_workload(tmp_path, field, value):
    """A wrong mode/workload must not silently select a non-comparable path."""
    with pytest.raises(MeasurementError, match=field):
        run_measurement(make_config(tmp_path, **{field: value}), FakeDeps.five_instances())


def test_rejects_duration_shorter_than_thirty_seconds(tmp_path):
    """A shortened interval would invalidate the specified stable window."""
    with pytest.raises(MeasurementError, match="at least 30"):
        run_measurement(make_config(tmp_path, duration_seconds=29), FakeDeps.five_instances())


def test_fails_when_a_serial_changes_during_the_run(tmp_path):
    """A replaced emulator must not be reported as the same five-instance run."""
    deps = FakeDeps.five_instances()
    deps.ready_checks[1] = SERIALS[:-1] + ("emulator-5564",)

    with pytest.raises(MeasurementError, match="serials changed"):
        run_measurement(make_config(tmp_path), deps)


def test_no_viewer_has_null_manual_fields_and_opens_no_browser(tmp_path):
    """Starting a viewer in no-viewer changes the workload being measured."""
    deps = FakeDeps.five_instances()
    result = run_measurement(make_config(tmp_path, workload="no-viewer"), deps)

    assert result["viewer_metrics"] == []
    assert result["manual_metrics"] == {
        "glass_to_glass_ms": None,
        "warm_switch_ms": None,
        "cold_switch_ms": None,
    }
    assert deps.opened == []


def test_no_viewer_rejects_unexpected_viewer_or_manual_submissions(tmp_path):
    """Unexpected submitted metrics prove a no-viewer run was contaminated."""
    deps = FakeDeps.five_instances()
    deps.non_viewer_submission = {"viewer_metrics": [1], "manual_metrics": {"glass_to_glass_ms": 1}}

    with pytest.raises(MeasurementError, match="no-viewer"):
        run_measurement(make_config(tmp_path, workload="no-viewer"), deps)


def test_one_viewer_requires_one_complete_numeric_record_per_serial(tmp_path):
    """A missing viewer record would hide a failing instance from the comparison."""
    deps = FakeDeps.five_instances()
    deps.viewer_metrics.pop(SERIALS[-1])

    with pytest.raises(MeasurementError, match="viewer metrics"):
        run_measurement(make_config(tmp_path), deps)


@pytest.mark.parametrize(
    ("mode", "family"), [("engine", "mediamtx"), ("legacy", "engine")]
)
def test_rejects_process_family_from_the_other_runtime(tmp_path, mode, family):
    """A mixed runtime would make before/after CPU data incomparable."""
    deps = FakeDeps.five_instances()
    deps.process_samples = [[ProcessSample(family, 1, 1)]]

    with pytest.raises(MeasurementError, match="unexpected"):
        run_measurement(make_config(tmp_path, mode=mode), deps)


def test_failure_diagnostics_are_bounded_and_redact_secrets(tmp_path):
    """An adapter failure must not leak credentials into durable evidence."""
    deps = FakeDeps.five_instances()
    deps.process_samples = [RuntimeError("token=top-secret " + "x" * 500)]

    with pytest.raises(MeasurementError) as error:
        run_measurement(make_config(tmp_path), deps)

    assert "top-secret" not in str(error.value)
    assert len(str(error.value)) < 320


def test_diagnostics_redact_json_and_bearer_secret_forms():
    """JSON-shaped and Bearer-form credentials must not enter durable failure JSON."""
    diagnostic = measurement._safe_detail('{"token":"json-secret"} Authorization: Bearer bearer-secret')

    assert "json-secret" not in diagnostic
    assert "bearer-secret" not in diagnostic
    assert "<redacted>" in diagnostic


def test_one_viewer_is_started_before_and_retained_during_process_sampling(tmp_path):
    """Moving the viewer after sampling would incorrectly record no-viewer CPU."""
    deps = FakeDeps.five_instances()

    run_measurement(make_config(tmp_path), deps)

    assert deps.events[0] == "viewer-start"
    assert all(event == "sample:True" for event in deps.events[1:-1])
    assert deps.events[-1] == "viewer-finish"


def test_process_family_totals_are_computed_per_snapshot_before_percentiles(tmp_path):
    """Flattening PIDs across time reports the wrong process-family median and RSS peak."""
    deps = FakeDeps.five_instances()
    deps.process_samples = [
        [ProcessSample("WindowControl", 1, 10), ProcessSample("engine", 2, 100), ProcessSample("engine", 3, 200)],
        [ProcessSample("WindowControl", 1, 20), ProcessSample("engine", 4, 300), ProcessSample("engine", 5, 400)],
    ]

    result = run_measurement(make_config(tmp_path), deps)

    assert result["processes"]["engine"] == {"cpu_median": 7, "cpu_p95": 8.8, "rss_peak": 700}
    assert result["processes"]["aggregate"]["cpu_median"] == 8


def test_real_adapter_creates_evidence_and_does_not_persist_raw_app_output(tmp_path, monkeypatch):
    """A nonexistent evidence directory must not stop app startup or leak its raw logs."""
    config = make_config(tmp_path / "repo", evidence_dir=tmp_path / "evidence")
    deps = RealMeasurementDeps(config)
    seen = {}

    class Child:
        def poll(self): return 0

    def popen(*args, **kwargs):
        seen.update(kwargs)
        return Child()

    monkeypatch.setattr(measurement.subprocess, "Popen", popen)
    child = deps.start_app({})

    assert isinstance(child, Child)
    assert config.evidence_dir.is_dir()
    assert seen["stdout"] is measurement.subprocess.DEVNULL
    assert not (config.evidence_dir / "window-control.log").exists()


def test_metrics_page_proxies_authenticated_selection_on_its_own_origin(tmp_path):
    """A separately served page must not make an unauthenticated cross-origin selection fetch."""
    requests = []

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append((self.path, self.headers.get("Cookie")))
            if self.path == "/login":
                self.send_response(200)
                self.send_header("Set-Cookie", "windowcontrol_session=accepted; Path=/; HttpOnly")
                self.end_headers()
                return
            if self.path == "/instances/emulator-5554/engine-select" and "windowcontrol_session=accepted" in (self.headers.get("Cookie") or ""):
                body = b'{"ok": true}'
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(401)

        def log_message(self, *_args): pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    page = tmp_path / "cutover_metrics_page.html"
    page.write_text("<script>fetch('/instances/x/select')</script>", encoding="utf-8")
    server = _MetricPageServer(page, f"http://127.0.0.1:{upstream.server_port}", "test-token")
    server.start()
    try:
        client = httpx.Client(base_url=server.origin)
        assert client.post("/measurement-login").status_code == 200
        selection = client.post("/instances/emulator-5554/engine-select")
        assert selection.status_code == 200
        assert selection.json() == {"ok": True}
        assert requests == [("/login", None), ("/instances/emulator-5554/engine-select", "windowcontrol_session=accepted")]
    finally:
        server.close()
        upstream.shutdown()
        thread.join(timeout=2)


def test_decision_refuses_pass_result_with_incomplete_measurement_schema(tmp_path):
    """Hashing a malformed PASS result would create an unjustified deletion approval."""
    files = []
    for mode, workload in (("legacy", "no-viewer"), ("legacy", "one-viewer"), ("engine", "no-viewer"), ("engine", "one-viewer")):
        path = tmp_path / f"{mode}-{workload}.json"
        path.write_text(json.dumps({"schema_version": 1, "result": "PASS", "mode": mode, "workload": workload, "serials": list(SERIALS)}), encoding="utf-8")
        files.append(path)

    with pytest.raises(MeasurementError, match="complete schema-v1"):
        record_cutover_decision(files, "APPROVE CUTOVER", tmp_path)

    assert not (tmp_path / "cutover-decision.json").exists()
