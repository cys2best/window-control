import hashlib
import itertools
import json
import os
from dataclasses import dataclass
import threading
import time
from pathlib import Path

import pytest

from scripts.verify_engine_cutover import (
    RECORDED_PERFORMANCE_OVERRIDE,
    CutoverConfig,
    CutoverError,
    CutoverFilePromptChannel,
    OwnedProcess,
    RealCutoverDeps,
    _BrowserProcess,
    main,
    run_cutover_verification,
    submit_file_confirmation,
)


SERIALS = (
    "emulator-5554",
    "emulator-5556",
    "emulator-5558",
    "emulator-5560",
    "emulator-5562",
)
_RUN_IDS = itertools.count()


def config(tmp_path, **changes):
    values = {
        "repo_root": tmp_path,
        "serials": SERIALS,
        "performance_evidence_dir": tmp_path / "performance",
        "evidence_dir": tmp_path / "engine" / "test" / f"cutover-run-{next(_RUN_IDS)}",
        "public_signaling_url": "wss://signal.example.com",
        "installer_path": tmp_path / "dist" / "WindowControl-Setup.exe",
        "performance_override": RECORDED_PERFORMANCE_OVERRIDE,
        "soak_hours": 8,
        "sample_interval_seconds": 60,
        "handshake_timeout_seconds": 15,
    }
    values.update(changes)
    return CutoverConfig(**values)


@dataclass
class BrowserSession:
    name: str
    pid: int
    started_at: float


class FakeDeps:
    def __init__(self):
        self.platform = "Windows"
        self.devices = list(SERIALS)
        self.ready_snapshots = []
        self.preexisting = []
        self.mutations = []
        self.events = []
        self.stopped = []
        self.owned_app = None
        self.owned_engines = []
        self.confirmations = {}
        self.health_values = [
            {"local_peers": 1, "public_peer": False},
            {"local_peers": 2, "public_peer": False},
            {"local_peers": 1, "public_peer": False},
            {"local_peers": 0, "public_peer": False},
        ]
        self.surface = {
            "selection_path": f"/instances/{SERIALS[0]}/select",
            "forbidden_routes": [],
            "legacy_processes": [],
            "legacy_dependencies": [],
            "legacy_assets": [],
        }
        self.selection_count = 0
        self.selection = {
            "ok": True,
            "id": f"adb:{SERIALS[0]}",
            "serial": SERIALS[0],
            "name": "instance0",
            "w": 1280,
            "h": 720,
            "whep_url": "http://100.64.1.4:51000/whep",
            "whep_token": "whep-secret",
            "signaling_url": "wss://signal.example.com",
            "signaling_token": "viewer-secret",
            "ice_servers": [],
            "generation": 4,
        }
        self.evidence_text = "all evidence is redacted"
        self.local_results = [
            {"video": True, "data_channel": True, "drag": True, "scroll_delta": 240},
            {"video": True, "data_channel": True, "drag": True, "scroll_delta": -180},
        ]
        self.race_result = {
            "winner": "local",
            "local_peers": 1,
            "public_peer": False,
            "loser_reaped": True,
        }
        self.switches = [
            {"index": index, "abandoned_reaped": True, "elapsed_seconds": 2}
            for index in range(20)
        ]
        dimensions = {
            "480": (270, 480),
            "720": (405, 720),
            "1080": (608, 1080),
            "1440": (810, 1440),
        }
        self.quality = [
            {
                "tier": tier,
                "resource_id": "resource-1",
                "peer_id": "peer-1",
                "generation": index + 5,
                "width": dimensions[tier][0],
                "height": dimensions[tier][1],
                "decoded_width": dimensions[tier][0],
                "decoded_height": dimensions[tier][1],
            }
            for index, tier in enumerate(("480", "720", "1080", "1440", "480"))
        ]
        self.scrcpy = {
            "before_generation": 9,
            "after_generation": 10,
            "before_engine_pid": 301,
            "after_engine_pid": 301,
            "before_whep_port": 51000,
            "after_whep_port": 51000,
            "before_peer_id": "peer-1",
            "after_peer_id": "peer-1",
            "video_resumed": True,
        }
        self.engine = {
            "before_engine_pid": 301,
            "after_engine_pid": 302,
            "before_whep_url": "http://100.64.1.4:51000/whep",
            "after_whep_url": "http://100.64.1.4:51001/whep",
            "before_whep_token": "old-secret",
            "after_whep_token": "new-secret",
            "fresh_select": True,
            "client_reconnected": True,
        }
        self.soak = {
            "status": "PASS",
            "elapsed_seconds": 8 * 60 * 60,
            "sample_interval_seconds": 60,
            "samples": [
                {
                    "sampled_at_seconds": index * 60,
                    "process_count": 6,
                    "peer_count": 5,
                    "forward_count": 5,
                    "cpu_percent": 35.0,
                    "rss_bytes": 500_000_000,
                    "frames_decoded": 10_000 + index,
                }
                for index in range(480)
            ],
        }
        self.public_websocket_url = (
            "wss://signal.example.com/?session=instance0&role=viewer&token=viewer-secret"
        )
        self.now = 0.0
        self.installer = {
            "installed": True,
            "launched_installed_executable": True,
            "firewall_program_rule": True,
            "firewall_path_matches_engine": True,
            "uninstalled": True,
            "cleanup_verified": True,
        }
        self.exit = {
            "tray_confirmed": True,
            "app_processes": 0,
            "owned_engine_processes": 0,
            "instance_forwards": 0,
        }

    def platform_name(self):
        return self.platform

    def ready_devices(self):
        snapshot = self.ready_snapshots.pop(0) if self.ready_snapshots else self.devices
        self.events.append(("ready", tuple(snapshot)))
        return list(snapshot)

    def preexisting_processes(self):
        return list(self.preexisting)

    def audit_cutover_surface(self, _repo_root, serial):
        self.events.append(("audit", serial))
        return dict(self.surface)

    def start_app(self, environment):
        self.mutations.append("start_app")
        self.started_environment = dict(environment)
        self.owned_app = OwnedProcess("app", 200, 20.0)
        return self.owned_app

    def wait_for_app(self, _app):
        return True

    def register_owned_engines(self, serials):
        self.events.append(("engine-register", tuple(serials)))
        self.owned_engines = [
            OwnedProcess("engine", 300 + index, 30.0 + index)
            for index, _serial in enumerate(serials)
        ]

    def select(self, serial):
        self.selection_count += 1
        selected = dict(self.selection)
        selected["request_path"] = f"/instances/{serial}/select"
        return selected

    def open_local_browser(self, _selection, name):
        index = len([event for event in self.events if event[0] == "local-open"])
        session = BrowserSession(name, 400 + index, 40.0 + index)
        self.events.append(("local-open", name))
        return session, dict(self.local_results[index])

    def engine_health(self, _serial):
        return dict(self.health_values.pop(0))

    def close_browser(self, session):
        self.events.append(("browser-close", session.name))

    def close_local_session(self, session):
        self.events.append(("session-close", session.name))
        return {"delete_observed": True}

    def open_public_browser(self, _selection, public_url):
        self.events.append(("public-open", public_url))
        return BrowserSession("public", 450, 45.0), {
            "video": True,
            "data_channel": True,
            "input": True,
        }

    def observed_public_websocket(self, _session):
        return self.public_websocket_url

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    def verify_mobile(self, _selection):
        return {
            "bearer_auth_enabled": True,
            "whep_authenticated": True,
            "video": True,
            "input": True,
        }

    def race_local_public(self, _selection, _timeout):
        return dict(self.race_result)

    def rapid_switches(self, serials, count, timeout):
        self.events.append(("switches", tuple(serials), count, timeout))
        return list(self.switches)

    def transition_quality(self, _serial, tiers):
        self.events.append(("quality", tuple(tiers)))
        return list(self.quality)

    def recover_scrcpy(self, _serial):
        self.events.append(("scrcpy-recovery", _serial))
        return dict(self.scrcpy)

    def recover_engine(self, _serial):
        self.events.append(("engine-recovery", _serial))
        return dict(self.engine)

    def run_soak(self, serials, hours, interval):
        self.events.append(("soak", tuple(serials), hours, interval))
        return dict(self.soak)

    def verify_installer(self, installer_path):
        self.events.append(("installer", installer_path))
        return dict(self.installer)

    def confirm(self, checkpoint, _message):
        return self.confirmations.get(checkpoint, "PASS")

    def tray_exit(self, _app):
        self.events.append(("tray-exit",))
        return dict(self.exit)

    def stop_owned(self, process):
        self.stopped.append((process.kind, process.pid, process.started_at))

    def cleanup_owned_helpers(self):
        for process in [*reversed(self.owned_engines), self.owned_app]:
            if process is not None:
                self.stop_owned(process)
        return len(self.stopped)

    def record_event(self, message):
        self.evidence_text += "\n" + message


def write_performance_evidence(directory, decision="APPROVE CUTOVER"):
    directory.mkdir(parents=True)
    paths = []
    for mode in ("legacy", "engine"):
        for workload in ("no-viewer", "one-viewer"):
            path = directory / f"{mode}-{workload}.json"
            empty_metrics = {"cpu_median": 0, "cpu_p95": 0, "rss_peak": 0}
            viewer_metrics = [
                {
                    "serial": serial,
                    "bits_per_second": 4_000_000,
                    "jitter_buffer_ms": 20,
                    "frames_per_second": 30,
                    "connected_at": "2026-09-01T00:00:00Z",
                    "switched_at": "2026-09-01T00:01:00Z",
                }
                for serial in SERIALS
            ] if workload == "one-viewer" else []
            path.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "mode": mode,
                        "workload": workload,
                        "commit": "a" * 40,
                        "serials": list(SERIALS),
                        "started_at": 1_000.0,
                        "duration_seconds": 60,
                        "processes": {
                            family: dict(empty_metrics)
                            for family in ("WindowControl", "engine", "mediamtx", "ffmpeg", "aggregate")
                        },
                        "viewer_metrics": viewer_metrics,
                        "manual_metrics": {
                            "glass_to_glass_ms": 120 if viewer_metrics else None,
                            "warm_switch_ms": 350 if viewer_metrics else None,
                            "cold_switch_ms": 900 if viewer_metrics else None,
                        },
                        "result": "PASS",
                    }
                ),
                encoding="utf-8",
            )
            paths.append(path)
    hashes = {
        str(path.resolve()): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }
    (directory / "cutover-decision.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "decision": decision,
                "reason": "operator accepted risk" if decision == "OVERRIDE CUTOVER" else None,
                "result_hashes": hashes,
            }
        ),
        encoding="utf-8",
    )


def test_refuses_non_windows_and_invalid_device_sets_before_mutation(tmp_path):
    deps = FakeDeps()
    deps.platform = "Darwin"
    with pytest.raises(CutoverError, match="Windows Host PC"):
        run_cutover_verification(config(tmp_path), deps)
    assert deps.mutations == []

    for devices in (SERIALS[:4], SERIALS + (SERIALS[-1],), (SERIALS[0],) * 5):
        deps = FakeDeps()
        deps.devices = list(devices)
        with pytest.raises(CutoverError, match="exactly five unique ready"):
            run_cutover_verification(config(tmp_path), deps)
        assert deps.mutations == []


def test_rechecks_exact_devices_immediately_before_first_mutation(tmp_path):
    deps = FakeDeps()
    deps.ready_snapshots = [list(SERIALS), list(SERIALS[:-1]) + ["emulator-5564"]]

    with pytest.raises(CutoverError, match="changed before mutation"):
        run_cutover_verification(config(tmp_path), deps)

    assert deps.mutations == []


def test_requires_hashed_four_way_performance_approval_without_recorded_override(tmp_path):
    deps = FakeDeps()
    run_config = config(tmp_path, performance_override=None)
    with pytest.raises(CutoverError, match="cutover-decision"):
        run_cutover_verification(run_config, deps)

    write_performance_evidence(run_config.performance_evidence_dir)
    result = run_cutover_verification(run_config, FakeDeps())
    assert result.checkpoints["performance"]["status"] == "PASS"

    result_file = run_config.performance_evidence_dir / "engine-one-viewer.json"
    result_file.write_text(result_file.read_text() + " ", encoding="utf-8")
    with pytest.raises(CutoverError, match="hash"):
        run_cutover_verification(run_config, FakeDeps())


def test_recorded_owner_override_is_labeled_overridden_not_measured_pass(tmp_path):
    result = run_cutover_verification(config(tmp_path), FakeDeps())

    assert result.checkpoints["performance"] == {
        "status": "OVERRIDDEN",
        "detail": RECORDED_PERFORMANCE_OVERRIDE,
    }
    assert result.summary["performance_gate"] == "OVERRIDDEN"


def test_measured_performance_rejects_hashed_but_incomplete_task1_payload(tmp_path):
    run_config = config(tmp_path, performance_override=None)
    write_performance_evidence(run_config.performance_evidence_dir)
    path = run_config.performance_evidence_dir / "engine-one-viewer.json"
    payload = json.loads(path.read_text())
    payload.pop("processes")
    path.write_text(json.dumps(payload), encoding="utf-8")
    decision_path = run_config.performance_evidence_dir / "cutover-decision.json"
    decision = json.loads(decision_path.read_text())
    decision["result_hashes"][str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
    decision_path.write_text(json.dumps(decision), encoding="utf-8")

    with pytest.raises(CutoverError, match="complete schema-v1"):
        run_cutover_verification(run_config, FakeDeps())


def test_rejects_staging_selection_legacy_surface_and_secret_leaks(tmp_path):
    deps = FakeDeps()
    deps.surface["selection_path"] = f"/instances/{SERIALS[0]}/engine-select"
    with pytest.raises(CutoverError, match="production /instances/.*/select"):
        run_cutover_verification(config(tmp_path), deps)

    for field, value in (
        ("forbidden_routes", ["/input"]),
        ("legacy_processes", ["ffmpeg.exe"]),
        ("legacy_dependencies", ["av"]),
        ("legacy_assets", ["mediamtx.exe"]),
    ):
        deps = FakeDeps()
        deps.surface[field] = value
        with pytest.raises(CutoverError, match="legacy cutover surface"):
            run_cutover_verification(config(tmp_path), deps)

    deps = FakeDeps()
    deps.evidence_text = f"request http://host/whep?token={deps.selection['whep_token']}"
    result = run_cutover_verification(config(tmp_path), deps)
    assert result.status == "FAIL"
    assert "whep-secret" not in json.dumps(result.to_dict())


def test_uses_production_select_and_validates_local_peer_lifecycle(tmp_path):
    deps = FakeDeps()
    result = run_cutover_verification(config(tmp_path), deps)

    assert result.checkpoints["local browser"]["status"] == "PASS"
    assert deps.selection_count >= 1
    assert [event for event in deps.events if event[0] in {"local-open", "session-close", "browser-close"}][:6] == [
        ("local-open", "local-1"),
        ("local-open", "local-2"),
        ("session-close", "local-1"),
        ("browser-close", "local-1"),
        ("session-close", "local-2"),
        ("browser-close", "local-2"),
    ]


def test_real_select_rejects_non_exact_production_response_before_adding_metadata(
    tmp_path, monkeypatch
):
    import scripts.verify_engine_cutover as verifier

    raw = {
        "ok": True,
        "id": "adb:emulator-5554",
        "serial": "emulator-5554",
        "name": "instance0",
        "w": 1280,
        "h": 720,
        "whep_url": "http://100.64.1.4:51000/whep",
        "whep_token": "whep-secret",
        "signaling_url": "wss://signal.example.com",
        "signaling_token": "viewer-secret",
        "ice_servers": [],
        "generation": 4,
    }

    class Response:
        status_code = 200

        def json(self):
            return dict(raw)

    monkeypatch.setenv("AUTH_TOKEN", "auth-secret")
    monkeypatch.setattr(verifier.httpx, "post", lambda *_args, **_kwargs: Response())
    deps = RealCutoverDeps(config(tmp_path))

    observed = deps.select(SERIALS[0])
    assert observed["w"] == 1280
    assert observed["h"] == 720
    assert observed["request_path"] == f"/instances/{SERIALS[0]}/select"

    raw["width"] = 1280
    with pytest.raises(CutoverError, match="exact 12-field"):
        deps.select(SERIALS[0])


def test_local_close_awaits_delete_and_polls_peer_count_before_browser_termination(tmp_path):
    deps = FakeDeps()
    deps.health_values = [
        {"local_peers": 1, "public_peer": False},
        {"local_peers": 2, "public_peer": False},
        {"local_peers": 2, "public_peer": False},
        {"local_peers": 1, "public_peer": False},
        {"local_peers": 1, "public_peer": False},
        {"local_peers": 0, "public_peer": False},
    ]

    result = run_cutover_verification(config(tmp_path), deps)

    assert result.checkpoints["local browser"]["status"] == "PASS"
    close_events = [
        event for event in deps.events
        if event[0] in {"session-close", "browser-close"} and event[1].startswith("local-")
    ]
    assert close_events == [
        ("session-close", "local-1"),
        ("browser-close", "local-1"),
        ("session-close", "local-2"),
        ("browser-close", "local-2"),
    ]


def test_local_close_requires_observed_whep_delete(tmp_path):
    deps = FakeDeps()
    deps.close_local_session = lambda _session: {"delete_observed": False}

    result = run_cutover_verification(config(tmp_path), deps)

    assert result.status == "FAIL"
    assert result.summary["failed_gate"] == "local browser"


@pytest.mark.parametrize(
    "health_values",
    [
        [{"local_peers": 1, "public_peer": False}] * 4,
        [
            {"local_peers": 1, "public_peer": False},
            {"local_peers": 2, "public_peer": False},
            {"local_peers": 2, "public_peer": False},
            {"local_peers": 0, "public_peer": False},
        ],
    ],
)
def test_local_peer_count_must_reach_two_then_return_to_zero(tmp_path, health_values):
    deps = FakeDeps()
    deps.health_values = health_values
    result = run_cutover_verification(config(tmp_path), deps)
    assert result.status == "FAIL"
    assert result.summary["failed_gate"] == "local browser"


def test_public_browser_uses_real_vps_and_exact_viewer_query_auth(tmp_path):
    deps = FakeDeps()
    result = run_cutover_verification(config(tmp_path), deps)
    assert result.checkpoints["public browser"]["status"] == "PASS"
    assert ("public-open", "wss://signal.example.com") in deps.events

    deps = FakeDeps()
    deps.public_websocket_url = (
        "wss://signal.example.com/?session=instance0&role=viewer&token=wrong"
    )
    failed = run_cutover_verification(config(tmp_path), deps)
    assert failed.status == "FAIL"
    assert failed.summary["failed_gate"] == "public browser"
    assert "viewer-secret" not in json.dumps(failed.to_dict())


@pytest.mark.parametrize(
    "actual_url",
    [
        "wss://signal.example.com/?session=other&role=viewer&token=viewer-secret",
        "wss://signal.example.com/?session=instance0&role=viewer&token=viewer-secret&extra=1",
        "wss://other.example.com/?session=instance0&role=viewer&token=viewer-secret",
    ],
)
def test_public_browser_compares_the_observed_websocket_url_exactly(tmp_path, actual_url):
    deps = FakeDeps()
    deps.public_websocket_url = actual_url

    result = run_cutover_verification(config(tmp_path), deps)

    assert result.status == "FAIL"
    assert result.summary["failed_gate"] == "public browser"


def test_public_browser_rejects_selection_from_a_different_signaling_vps(tmp_path):
    """Passing config separately must not hide a stale engine signaling target."""
    deps = FakeDeps()
    deps.selection["signaling_url"] = "wss://stale.example.com"

    result = run_cutover_verification(config(tmp_path), deps)

    assert result.status == "FAIL"
    assert result.summary["failed_gate"] == "public browser"


def test_mobile_requires_bearer_whep_video_and_input(tmp_path):
    result = run_cutover_verification(config(tmp_path), FakeDeps())
    assert result.checkpoints["mobile"]["status"] == "PASS"

    deps = FakeDeps()
    deps.verify_mobile = lambda _selection: {
        "bearer_auth_enabled": False,
        "whep_authenticated": True,
        "video": True,
        "input": True,
    }
    failed = run_cutover_verification(config(tmp_path), deps)
    assert failed.status == "FAIL"
    assert failed.summary["failed_gate"] == "mobile"


def test_race_and_twenty_switches_require_bounded_loser_cleanup(tmp_path):
    result = run_cutover_verification(config(tmp_path), FakeDeps())
    assert result.checkpoints["local/public race"]["status"] == "PASS"
    assert result.checkpoints["rapid switches"]["status"] == "PASS"

    deps = FakeDeps()
    deps.race_result["loser_reaped"] = False
    assert run_cutover_verification(config(tmp_path), deps).summary["failed_gate"] == "local/public race"

    deps = FakeDeps()
    deps.switches[13]["elapsed_seconds"] = 16
    assert run_cutover_verification(config(tmp_path), deps).summary["failed_gate"] == "rapid switches"


def test_quality_ladder_stays_on_one_resource_and_advances_generation_and_dimensions(tmp_path):
    result = run_cutover_verification(config(tmp_path), FakeDeps())
    assert result.checkpoints["quality ladder"]["status"] == "PASS"

    deps = FakeDeps()
    deps.quality[3]["peer_id"] = "replacement"
    failed = run_cutover_verification(config(tmp_path), deps)
    assert failed.summary["failed_gate"] == "quality ladder"


def test_quality_ladder_requires_each_requested_tier_dimension(tmp_path):
    deps = FakeDeps()
    for item, dimension in zip(deps.quality, (1, 2, 3, 4, 1)):
        item.update(
            width=dimension,
            height=dimension,
            decoded_width=dimension,
            decoded_height=dimension,
        )

    result = run_cutover_verification(config(tmp_path), deps)

    assert result.status == "FAIL"
    assert result.summary["failed_gate"] == "quality ladder"

    deps = FakeDeps()
    deps.quality[3]["resource_id"] = "replacement-resource"
    failed = run_cutover_verification(config(tmp_path), deps)
    assert failed.summary["failed_gate"] == "quality ladder"

    deps = FakeDeps()
    for item in deps.quality:
        item["resource_id"] = ""
    failed = run_cutover_verification(config(tmp_path), deps)
    assert failed.summary["failed_gate"] == "quality ladder"

    deps = FakeDeps()
    for item in deps.quality:
        item.update(width=270, height=480, decoded_width=270, decoded_height=480)
    failed = run_cutover_verification(config(tmp_path), deps)
    assert failed.summary["failed_gate"] == "quality ladder"

    deps = FakeDeps()
    deps.quality[-1].update(width=405, height=720, decoded_width=405, decoded_height=720)
    failed = run_cutover_verification(config(tmp_path), deps)
    assert failed.summary["failed_gate"] == "quality ladder"


def test_scrcpy_and_engine_recovery_enforce_identity_contracts(tmp_path):
    result = run_cutover_verification(config(tmp_path), FakeDeps())
    assert result.checkpoints["scrcpy recovery"]["status"] == "PASS"
    assert result.checkpoints["engine recovery"]["status"] == "PASS"

    deps = FakeDeps()
    deps.scrcpy["after_engine_pid"] = 999
    assert run_cutover_verification(config(tmp_path), deps).summary["failed_gate"] == "scrcpy recovery"

    deps = FakeDeps()
    deps.engine["after_whep_token"] = deps.engine["before_whep_token"]
    assert run_cutover_verification(config(tmp_path), deps).summary["failed_gate"] == "engine recovery"


def test_soak_requires_eight_hours_five_instances_and_minute_samples(tmp_path):
    result = run_cutover_verification(config(tmp_path), FakeDeps())
    assert result.checkpoints["soak"]["status"] == "PASS"

    deps = FakeDeps()
    shortened = run_cutover_verification(config(tmp_path, soak_hours=7.99), deps)
    assert shortened.status == "INCOMPLETE"
    assert shortened.checkpoints["soak"]["status"] == "INCOMPLETE"

    deps = FakeDeps()
    deps.soak["samples"][10].pop("rss_bytes")
    failed = run_cutover_verification(config(tmp_path), deps)
    assert failed.summary["failed_gate"] == "soak"

    deps = FakeDeps()
    deps.soak["samples"][20]["sampled_at_seconds"] += 7
    failed = run_cutover_verification(config(tmp_path), deps)
    assert failed.summary["failed_gate"] == "soak"

    deps = FakeDeps()
    extra = dict(deps.soak["samples"][-1])
    extra.update(sampled_at_seconds=8 * 60 * 60, frames_decoded=20_000)
    deps.soak["samples"].append(extra)
    failed = run_cutover_verification(config(tmp_path), deps)
    assert failed.summary["failed_gate"] == "soak"

    deps = FakeDeps()
    for sample in deps.soak["samples"]:
        sample["frames_decoded"] = 10
    failed = run_cutover_verification(config(tmp_path), deps)
    assert failed.summary["failed_gate"] == "soak"


def test_short_actual_soak_is_incomplete_not_failed(tmp_path):
    deps = FakeDeps()
    deps.soak.update(status="INCOMPLETE", elapsed_seconds=7.5 * 60 * 60)
    deps.soak["samples"] = deps.soak["samples"][:450]

    result = run_cutover_verification(config(tmp_path), deps)

    assert result.status == "INCOMPLETE"
    assert result.checkpoints["soak"]["status"] == "INCOMPLETE"
    assert "failed_gate" not in result.summary

def test_installer_firewall_uninstall_and_tray_exit_cleanup_are_required(tmp_path):
    result = run_cutover_verification(config(tmp_path), FakeDeps())
    assert result.checkpoints["installer"]["status"] == "PASS"
    assert result.checkpoints["tray exit"]["status"] == "PASS"

    deps = FakeDeps()
    deps.installer["firewall_path_matches_engine"] = False
    assert run_cutover_verification(config(tmp_path), deps).summary["failed_gate"] == "installer"

    deps = FakeDeps()
    deps.exit["instance_forwards"] = 1
    assert run_cutover_verification(config(tmp_path), deps).summary["failed_gate"] == "tray exit"


def test_any_manual_failure_or_skip_never_reports_pass(tmp_path):
    deps = FakeDeps()
    deps.confirmations["mobile"] = "FAIL"
    result = run_cutover_verification(config(tmp_path), deps)
    assert result.status == "FAIL"

    deps = FakeDeps()
    deps.confirmations["public browser"] = "SKIP"
    result = run_cutover_verification(config(tmp_path), deps)
    assert result.status == "INCOMPLETE"


def test_failure_writes_redacted_partial_json_and_cleans_only_started_helpers(tmp_path):
    deps = FakeDeps()
    deps.local_results[0]["video"] = False
    run_config = config(tmp_path, keep_on_failure=True)
    result = run_cutover_verification(run_config, deps)

    assert result.status == "FAIL"
    saved = json.loads((run_config.evidence_dir / "result.json").read_text())
    assert saved["summary"]["failed_gate"] == "local browser"
    assert "whep-secret" not in json.dumps(saved)
    assert all(kind != "app" for kind, _pid, _started in deps.stopped)
    assert result.summary["retained_on_failure"]["keep_on_failure"] is True


@pytest.mark.parametrize("outcome", ["FAIL", "INCOMPLETE"])
def test_failed_or_incomplete_run_cleans_exact_owned_helpers_unless_retention_requested(
    tmp_path, outcome
):
    deps = FakeDeps()
    if outcome == "FAIL":
        deps.local_results[0]["video"] = False
    else:
        deps.confirmations["public browser"] = "SKIP"

    result = run_cutover_verification(config(tmp_path), deps)

    assert result.status == outcome
    assert deps.stopped == [
        ("engine", 304, 34.0),
        ("engine", 303, 33.0),
        ("engine", 302, 32.0),
        ("engine", 301, 31.0),
        ("engine", 300, 30.0),
        ("app", 200, 20.0),
    ]
    assert "retained_on_failure" not in result.summary
    assert result.summary["cleanup_on_failure"]["exact_owned_helpers_stopped"] == 6


def test_child_processes_receive_one_sanitized_environment_without_parent_mutation(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "unsafe-parent-path")
    monkeypatch.setenv("AUTH_TOKEN", "auth-secret")
    monkeypatch.setenv("TUNNEL_SECRET", "tunnel-secret")
    monkeypatch.setenv("ENGINE_SIGNALING_SECRET", "signaling-secret")
    monkeypatch.setenv("TURN_CREDENTIAL", "turn-secret")
    before = dict(os.environ)
    deps = FakeDeps()

    run_cutover_verification(config(tmp_path), deps)

    assert "PYTHONPATH" not in deps.started_environment
    assert deps.started_environment["AUTH_TOKEN"] == "auth-secret"
    assert deps.started_environment["VPS_SIGNALING_URL"] == "wss://signal.example.com"
    assert deps.started_environment["ENGINE_EXE_PATH"] == str(
        tmp_path / "engine" / "build" / "Release" / "engine.exe"
    )
    assert os.environ == before


def test_file_prompts_are_nonce_pid_and_start_time_scoped(tmp_path, monkeypatch):
    import scripts.verify_engine_cutover as verifier

    repo_root = tmp_path / "repo"
    evidence_dir = repo_root / "engine" / "test" / "engine-cutover-live"
    evidence_dir.mkdir(parents=True)
    monkeypatch.setattr(verifier, "_pid_started_at", lambda pid: 123.5)
    channel = CutoverFilePromptChannel(
        evidence_dir,
        verifier_pid=404,
        poll_seconds=0.01,
    )
    answers = []
    thread = threading.Thread(
        target=lambda: answers.append(channel.prompt("Confirm real video", "local browser")),
        daemon=True,
    )
    thread.start()
    prompt_path = evidence_dir / "active-prompt.json"
    deadline = time.monotonic() + 2
    while not prompt_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    prompt = json.loads(prompt_path.read_text())
    assert prompt["verifier_pid"] == 404
    assert prompt["verifier_started_at"] == 123.5
    assert prompt["nonce"]

    submit_file_confirmation(repo_root, "PASS")
    thread.join(timeout=2)

    assert answers == ["PASS"]
    assert not prompt_path.exists()
    assert not list(evidence_dir.glob("prompt-response-*.json"))


def test_real_adapter_requires_auth_tunnel_signaling_and_turn_secrets(tmp_path, monkeypatch):
    deps = RealCutoverDeps(config(tmp_path))
    for name in (
        "AUTH_TOKEN",
        "TUNNEL_SECRET",
        "ENGINE_SIGNALING_SECRET",
        "TURN_CREDENTIAL",
        "PUBLIC_UI_URL",
    ):
        monkeypatch.delenv(name, raising=False)

    with pytest.raises(CutoverError, match="required environment"):
        deps.validate_required_environment()

    for name in (
        "AUTH_TOKEN",
        "TUNNEL_SECRET",
        "ENGINE_SIGNALING_SECRET",
        "TURN_CREDENTIAL",
        "PUBLIC_UI_URL",
    ):
        monkeypatch.setenv(name, f"{name.lower()}-value")
    engine = tmp_path / "engine" / "build" / "Release" / "engine.exe"
    engine.parent.mkdir(parents=True)
    engine.touch()
    deps.validate_required_environment()


def test_cli_refuses_non_windows_without_creating_evidence(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("scripts.verify_engine_cutover.platform.system", lambda: "Darwin")
    evidence = tmp_path / "evidence"

    exit_code = main(
        [
            "--repo-root", str(tmp_path),
            "--serials", *SERIALS,
            "--performance-evidence-dir", str(tmp_path / "performance"),
            "--evidence-dir", str(evidence),
            "--public-signaling-url", "wss://signal.example.com",
            "--installer-path", str(tmp_path / "installer.exe"),
            "--performance-override", RECORDED_PERFORMANCE_OVERRIDE,
        ]
    )

    assert exit_code == 1
    assert "Windows Host PC" in capsys.readouterr().out
    assert not evidence.exists()


def test_real_soak_runtime_count_excludes_owned_browser_helpers(tmp_path, monkeypatch):
    deps = RealCutoverDeps(config(tmp_path))
    deps._owned_app = OwnedProcess("app", 200, 20.0)
    deps._active_browsers = [object(), object(), object()]
    monkeypatch.setattr("scripts.verify_engine_cutover._pid_started_at", lambda pid: 20.0 if pid == 200 else None)

    assert deps._runtime_process_count([object()] * 5) == 6


def test_real_soak_uses_absolute_minute_deadlines_with_collection_overhead(tmp_path, monkeypatch):
    class Clock:
        now = 1000.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    class Engine:
        def cpu_percent(self, interval=None):
            assert interval is None
            return 1.0

        class Memory:
            rss = 100

        def memory_info(self):
            return self.Memory()

    run_config = config(tmp_path)
    run_config.evidence_dir.mkdir(parents=True)
    deps = RealCutoverDeps(run_config)
    deps._active_browsers = [object()] * 5
    clock = Clock()
    engines = {serial: Engine() for serial in SERIALS}
    frames = itertools.count(100)
    monkeypatch.setattr("scripts.verify_engine_cutover.time.monotonic", clock.monotonic)
    monkeypatch.setattr("scripts.verify_engine_cutover.time.sleep", clock.sleep)
    monkeypatch.setattr(deps, "_engine_process", lambda serial: engines[serial])
    monkeypatch.setattr(
        deps,
        "engine_health",
        lambda _serial: {"local_peers": 1, "public_peer": False},
    )
    monkeypatch.setattr(
        deps,
        "_decode_stats",
        lambda _browser: {"frames_decoded": next(frames)},
    )
    monkeypatch.setattr(deps, "_runtime_process_count", lambda _engines: 6)

    def collect_forwards(_serials):
        clock.now += 7
        return 5

    monkeypatch.setattr(deps, "_forward_count", collect_forwards)

    soak = deps.run_soak(SERIALS, 8, 60)

    assert soak["status"] == "PASS"
    assert len(soak["samples"]) == 480
    assert [sample["sampled_at_seconds"] for sample in soak["samples"]] == [
        index * 60 for index in range(480)
    ]
    assert soak["elapsed_seconds"] == 8 * 60 * 60


def test_real_engine_recovery_fails_closed_when_registered_pid_is_replaced(tmp_path, monkeypatch):
    deps = RealCutoverDeps(config(tmp_path))
    deps._owned_engines[SERIALS[0]] = OwnedProcess("engine", 301, 30.0)
    terminated = []

    class ReplacedProcess:
        pid = 301

        def terminate(self):
            terminated.append(self.pid)

    monkeypatch.setattr("scripts.verify_engine_cutover._pid_started_at", lambda _pid: 31.0)
    monkeypatch.setattr("psutil.Process", lambda _pid: ReplacedProcess())

    with pytest.raises(CutoverError, match="replaced"):
        deps.recover_engine(SERIALS[0])

    assert terminated == []


def test_real_engine_registration_rejects_matching_process_outside_owned_app_tree(tmp_path, monkeypatch):
    deps = RealCutoverDeps(config(tmp_path))
    deps._owned_app = OwnedProcess("app", 200, 20.0)

    class Process:
        def __init__(self, pid, started_at):
            self.pid = pid
            self._started_at = started_at

        def create_time(self):
            return self._started_at

    app_engine = Process(301, 30.0)
    unrelated_engine = Process(999, 99.0)
    monkeypatch.setattr(
        deps,
        "_find_engine_candidates",
        lambda _serial: [app_engine, unrelated_engine],
    )
    monkeypatch.setattr(deps, "_app_descendant_pids", lambda: {301}, raising=False)

    deps.register_owned_engines((SERIALS[0],))

    assert deps._owned_engines[SERIALS[0]] == OwnedProcess("engine", 301, 30.0)


def test_real_browser_transport_observation_comes_from_cdp_network_identity(tmp_path, monkeypatch):
    deps = RealCutoverDeps(config(tmp_path))
    handle = object()
    monkeypatch.setattr(
        deps,
        "_cdp",
        lambda *_args, **_kwargs: {
            "result": {
                "value": {
                    "resource_ids": ["http://127.0.0.1:51000/whep/resource-7"],
                    "delete_urls": [],
                    "websocket_urls": [
                        "wss://signal.example.com/?session=instance0&role=viewer&token=viewer-secret"
                    ],
                }
            }
        },
    )

    observed = deps._browser_transport_observation(handle)

    assert observed["resource_ids"] == ["http://127.0.0.1:51000/whep/resource-7"]
    assert deps.observed_public_websocket(handle).endswith("token=viewer-secret")


def test_real_local_close_awaits_production_close_and_matches_delete_location(tmp_path, monkeypatch):
    deps = RealCutoverDeps(config(tmp_path))
    handle = object()
    resource = "http://127.0.0.1:51000/whep/resource-7"
    calls = []
    monkeypatch.setattr(deps, "_active_resource_id", lambda _handle: resource)
    monkeypatch.setattr(
        deps,
        "_browser_transport_observation",
        lambda _handle: {
            "resource_ids": [resource],
            "delete_urls": [resource],
            "websocket_urls": [],
        },
    )

    def cdp(_handle, method, params):
        calls.append((method, params))
        return {"result": {"value": True}}

    monkeypatch.setattr(deps, "_cdp", cdp)

    observed = deps.close_local_session(handle)

    assert observed == {"delete_observed": True}
    assert calls[0][0] == "Runtime.evaluate"
    assert calls[0][1]["awaitPromise"] is True
    assert "closeEngineInstance" in calls[0][1]["expression"]


def test_real_public_query_observation_ignores_unrelated_browser_websockets(tmp_path, monkeypatch):
    deps = RealCutoverDeps(config(tmp_path))
    handle = object()
    expected = "wss://signal.example.com/?session=instance0&role=viewer&token=viewer-secret"
    monkeypatch.setattr(
        deps,
        "_browser_transport_observation",
        lambda _handle: {
            "resource_ids": [],
            "delete_urls": [],
            "websocket_urls": ["wss://telemetry.example.com/socket", expected],
        },
    )

    assert deps.observed_public_websocket(handle) == expected


def test_real_public_query_observation_uses_the_most_recent_reconnect(tmp_path, monkeypatch):
    # The production client reconnects (fresh /select, fresh token, fresh
    # WebSocket) on every tab visibilitychange, so an operator switching to
    # DevTools to inspect signaling legitimately produces more than one
    # matching request. The most recent one is the live connection.
    deps = RealCutoverDeps(config(tmp_path))
    handle = object()
    first = "wss://signal.example.com/?session=instance0&role=viewer&token=stale-secret"
    latest = "wss://signal.example.com/?session=instance0&role=viewer&token=fresh-secret"
    monkeypatch.setattr(
        deps,
        "_browser_transport_observation",
        lambda _handle: {
            "resource_ids": [],
            "delete_urls": [],
            "websocket_urls": [first, latest],
        },
    )

    assert deps.observed_public_websocket(handle) == latest


def test_real_firewall_cleanup_check_is_bound_to_the_installed_engine_path(tmp_path):
    deps = RealCutoverDeps(config(tmp_path))
    engine = Path(r"C:\Program Files\WindowControl\assets\engine\engine.exe")

    assert deps._firewall_contains_engine(
        r'{"Program":"C:\\Program Files\\WindowControl\\assets\\engine\\engine.exe"}',
        engine,
    )
    assert not deps._firewall_contains_engine("[]", engine)


@pytest.mark.parametrize("name", ["engine.exe", "WindowControl.exe"])
def test_real_preflight_treats_installed_app_and_engine_as_unowned(name):
    assert RealCutoverDeps._is_forbidden_preexisting_name(name)


def test_real_preflight_diagnostics_do_not_create_evidence_before_mutation(tmp_path):
    run_config = config(tmp_path)
    deps = RealCutoverDeps(run_config)

    deps.record_event("read-only preflight")
    assert not run_config.evidence_dir.exists()

    run_config.evidence_dir.mkdir(parents=True)
    deps.activate_evidence()
    assert "read-only preflight" in (run_config.evidence_dir / "verification.log").read_text()


def test_real_start_app_captures_stdout_and_stderr_to_evidence_dir(tmp_path, monkeypatch):
    import subprocess as subprocess_module

    run_config = config(tmp_path)
    deps = RealCutoverDeps(run_config)
    run_config.evidence_dir.mkdir(parents=True)

    captured = {}

    class FakeProcess:
        def __init__(self):
            self.pid = 4321

    def fake_popen(_command, **kwargs):
        captured.update(kwargs)
        return FakeProcess()

    monkeypatch.setattr(subprocess_module, "Popen", fake_popen)

    class FakePsutilProcess:
        def __init__(self, _pid):
            pass

        def create_time(self):
            return 200.0

    monkeypatch.setattr("psutil.Process", FakePsutilProcess)

    deps.start_app({})

    assert captured["stderr"] == subprocess_module.STDOUT
    log_stream = captured["stdout"]
    assert Path(log_stream.name) == run_config.evidence_dir / "app.log"
    assert log_stream.closed, "parent must close its handle after spawning so the child owns the fd"


def test_real_public_page_url_is_derived_https_not_the_raw_tunnel_websocket_env(monkeypatch):
    monkeypatch.setenv("PUBLIC_UI_URL", "wss://tunnel.koeeru.com/__tunnel/register")

    assert RealCutoverDeps._public_page_url() == "https://tunnel.koeeru.com"


def test_real_open_public_browser_navigates_to_the_derived_page_not_the_websocket_url(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("PUBLIC_UI_URL", "wss://tunnel.koeeru.com/__tunnel/register")
    run_config = config(tmp_path, public_signaling_url="wss://signal.example.com")
    deps = RealCutoverDeps(run_config)
    handle = object()
    captured = {}

    def start(name, url, *, local_only=False):
        captured.update(name=name, url=url, local_only=local_only)
        return handle

    monkeypatch.setattr(deps, "_start_browser", start)
    monkeypatch.setattr(deps, "confirm", lambda *_args: "PASS")
    monkeypatch.setattr(
        deps,
        "_decode_stats",
        lambda _handle: {"frames_decoded": 1, "width": 1280, "height": 720, "peers": 1},
    )

    deps.open_public_browser({}, "wss://signal.example.com")

    assert captured["url"] == "https://tunnel.koeeru.com"


def test_real_scrcpy_recovery_uses_the_launcher_exact_process_pattern(tmp_path):
    from server.scrcpy_server import scrcpy_server_process_pattern

    deps = RealCutoverDeps(config(tmp_path))

    assert deps._scrcpy_kill_pattern(10) == scrcpy_server_process_pattern(10)


def test_real_local_browser_forces_local_transport_while_using_production_assets(tmp_path, monkeypatch):
    deps = RealCutoverDeps(config(tmp_path))
    captured = {}
    handle = object()

    def start(name, url, *, local_only=False):
        captured.update(name=name, url=url, local_only=local_only)
        return handle

    monkeypatch.setattr(deps, "_start_browser", start)
    monkeypatch.setattr(deps, "confirm", lambda *_args: "PASS")
    monkeypatch.setattr(
        deps,
        "_decode_stats",
        lambda _handle: {"frames_decoded": 1, "width": 1280, "height": 720, "peers": 1},
    )

    deps.open_local_browser({}, "local-1")

    assert captured == {
        "name": "local-1",
        "url": f"http://127.0.0.1:{deps.config.app_port}/",
        "local_only": True,
    }


def test_real_start_browser_enables_page_and_injects_tracker_over_one_session(tmp_path, monkeypatch):
    import subprocess as subprocess_module

    deps = RealCutoverDeps(config(tmp_path))
    monkeypatch.setattr(deps, "_find_browser", lambda: Path("msedge.exe"))
    monkeypatch.setattr(deps, "_free_port", lambda: 51999)

    class FakeProcess:
        def __init__(self, *_args, **_kwargs):
            self.pid = 4242

    monkeypatch.setattr(subprocess_module, "Popen", FakeProcess)

    class FakePsutilProcess:
        def __init__(self, pid):
            self.pid = pid

        def create_time(self):
            return 100.0

        def children(self, recursive=True):
            return []

    monkeypatch.setattr("psutil.Process", FakePsutilProcess)

    class FakeResponse:
        def json(self):
            return [{
                "type": "page",
                "id": "target-1",
                "webSocketDebuggerUrl": "ws://127.0.0.1:51999/devtools/page/target-1",
            }]

    monkeypatch.setattr("httpx.get", lambda *_args, **_kwargs: FakeResponse())
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)

    sessions = []

    def fake_cdp_session(_handle, calls):
        sessions.append([method for method, _params in calls])

    monkeypatch.setattr(deps, "_cdp_session", fake_cdp_session)

    deps._start_browser("local-1", "http://127.0.0.1:8000/")

    assert len(sessions) == 1, "Page.enable, tracker injection, and navigate must share one CDP session"
    assert sessions[0] == [
        "Page.enable",
        "Page.addScriptToEvaluateOnNewDocument",
        "Page.navigate",
    ]


def test_real_cdp_session_reuses_one_websocket_connection_across_calls(tmp_path, monkeypatch):
    deps = RealCutoverDeps(config(tmp_path))
    handle = _BrowserProcess(
        name="local-1",
        process=None,
        started_at=100.0,
        profile_dir=tmp_path,
        debug_port=51999,
        target_id="target-1",
        websocket_url="ws://127.0.0.1:51999/devtools/page/target-1",
        owned_pids=[],
    )

    connect_calls = []

    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def fake_connect(url, **_kwargs):
        connect_calls.append(url)
        return FakeSocket()

    monkeypatch.setattr(
        "websockets.sync.client.connect", fake_connect
    )
    monkeypatch.setattr(
        RealCutoverDeps,
        "_cdp_call",
        staticmethod(lambda _socket, method, _params: {"method": method}),
    )

    deps._cdp_session(
        handle,
        [("Page.enable", {}), ("Page.navigate", {"url": "http://x/"})],
    )

    assert connect_calls == [handle.websocket_url]


def test_real_rapid_switch_gate_checks_each_abandoned_engine(tmp_path, monkeypatch):
    deps = RealCutoverDeps(config(tmp_path))
    prompts = []
    health_calls = []
    monkeypatch.setattr(
        deps,
        "confirm",
        lambda checkpoint, _message: prompts.append(checkpoint) or "PASS",
    )
    monkeypatch.setattr(
        deps,
        "engine_health",
        lambda serial: health_calls.append(serial) or {"local_peers": 0, "public_peer": False},
    )

    records = deps.rapid_switches(SERIALS, 3, 15)

    assert prompts == ["rapid switch 1/3", "rapid switch 2/3", "rapid switch 3/3"]
    assert health_calls == [SERIALS[0], SERIALS[1], SERIALS[2]]
    assert all(record["abandoned_reaped"] for record in records)


def test_real_peer_identity_requires_one_exact_browser_peer(tmp_path):
    deps = RealCutoverDeps(config(tmp_path))

    assert deps._one_peer_id({"peer_ids": ["peer-1"]}) == "peer-1"
    with pytest.raises(CutoverError, match="exactly one"):
        deps._one_peer_id({"peer_ids": ["peer-1", "peer-2"]})


def test_real_installer_gate_requires_source_tray_exit_before_install(tmp_path, monkeypatch):
    deps = RealCutoverDeps(config(tmp_path))
    deps._owned_app = OwnedProcess("app", 200, 20.0)
    prompts = []
    monkeypatch.setattr(
        deps,
        "confirm",
        lambda checkpoint, _message: prompts.append(checkpoint) or "PASS",
    )
    monkeypatch.setattr(deps, "_wait_owned_exit", lambda _process, _timeout: True)

    deps._require_source_tray_exit_for_installer()

    assert prompts == ["source tray exit before installer"]


def test_real_cleanup_terminates_only_exact_registered_pid_start_time_pairs(
    tmp_path, monkeypatch
):
    import psutil
    import scripts.verify_engine_cutover as verifier

    deps = RealCutoverDeps(config(tmp_path))
    deps._owned_app = OwnedProcess("app", 200, 20.0)
    deps._owned_engines = {
        SERIALS[0]: OwnedProcess("engine", 300, 30.0),
        SERIALS[1]: OwnedProcess("engine", 301, 31.0),
    }
    live_started = {200: 20.0, 300: 30.0, 301: 99.0}
    events = []

    class Process:
        def __init__(self, pid):
            self.pid = pid

        def terminate(self):
            events.append(("terminate", self.pid))
            live_started[self.pid] = None

        def wait(self, timeout):
            events.append(("wait", self.pid, timeout))

    monkeypatch.setattr(verifier, "_pid_started_at", lambda pid: live_started.get(pid))
    monkeypatch.setattr(psutil, "Process", Process)

    assert deps.cleanup_owned_helpers() == 2
    assert events == [
        ("terminate", 300),
        ("wait", 300, 10),
        ("terminate", 200),
        ("wait", 200, 10),
    ]
