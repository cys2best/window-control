"""Synthetic assessor tests; these are not captured Windows/iPhone runs."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.validate_remote_stream import evaluate_run


def full_hd_fixture():
    return [{
        "elapsed_s": second,
        "source_width": 1920, "source_height": 1080,
        "decoded_width": 1920, "decoded_height": 1080,
        "decoded_fps": 30, "target_bitrate_mbps": 8, "bitrate_mbps": 4,
        "adaptive_downgrades": 0, "total_freeze_s": 0, "max_freeze_s": 0,
        "route": "relay", "address_family": "IPv4", "relay_protocol": "tls",
    } for second in range(601)]


def test_complete_ten_minute_evidence_passes_without_static_scene_bitrate_floor():
    result = evaluate_run(12, full_hd_fixture(), "turn_tls")
    assert result["status"] == "pass"
    assert result["reasons"] == []
    assert result["metrics"]["duration_s"] == 600
    assert result["metrics"]["freeze_fraction"] == 0


@pytest.mark.parametrize("field,value", [
    ("source_width", 1080), ("decoded_height", 608), ("decoded_fps", 0),
    ("target_bitrate_mbps", 1.7), ("adaptive_downgrades", 1),
    ("max_freeze_s", 1.1), ("relay_protocol", "tcp"), ("route", "direct"),
])
def test_measured_acceptance_violation_fails(field, value):
    samples = full_hd_fixture()
    for sample in samples[300:]:
        sample[field] = value
    assert evaluate_run(12, samples, "turn_tls")["status"] == "fail"


def test_exact_freeze_boundaries():
    samples = full_hd_fixture()
    samples[-1]["max_freeze_s"] = 1
    samples[-1]["total_freeze_s"] = 1
    assert evaluate_run(12, samples, "turn_tls")["status"] == "pass"
    samples[-1]["total_freeze_s"] = 6
    assert evaluate_run(12, samples, "turn_tls")["status"] == "fail"


def test_missing_freeze_evidence_and_observation_gap_are_inconclusive():
    samples = full_hd_fixture()
    samples[300]["max_freeze_s"] = None
    assert evaluate_run(12, samples, "turn_tls")["status"] == "inconclusive"
    gapped = copy.deepcopy(full_hd_fixture())
    del gapped[300:304]
    assert evaluate_run(12, gapped, "turn_tls")["status"] == "inconclusive"


def test_gap_boundary_uses_real_one_second_cadence():
    samples = full_hd_fixture()
    del samples[300]
    assert evaluate_run(12, samples, "turn_tls")["status"] == "pass"
    sparse = full_hd_fixture()[::3]
    assert evaluate_run(12, sparse, "turn_tls")["status"] == "inconclusive"


@pytest.mark.parametrize("field", [
    "source_width", "source_height", "decoded_width", "decoded_height",
    "decoded_fps", "target_bitrate_mbps", "adaptive_downgrades",
    "total_freeze_s", "max_freeze_s", "route", "address_family", "relay_protocol",
])
@pytest.mark.parametrize("missing", [True, False])
def test_missing_required_observations_are_inconclusive(field, missing):
    samples = full_hd_fixture()
    if missing:
        del samples[300][field]
    else:
        samples[300][field] = None
    assert evaluate_run(12, samples, "turn_tls")["status"] == "inconclusive"


@pytest.mark.parametrize("field,value", [
    ("elapsed_s", 299), ("elapsed_s", 298), ("decoded_fps", float("nan")),
    ("bitrate_mbps", float("inf")), ("adaptive_downgrades", -1),
    ("total_freeze_s", -1), ("max_freeze_s", -1), ("decoded_fps", True),
    ("adaptive_downgrades", 0.5), ("source_width", "1920"),
])
def test_invalid_numeric_evidence_is_inconclusive(field, value):
    samples = full_hd_fixture()
    samples[300][field] = value
    assert evaluate_run(12, samples, "turn_tls")["status"] == "inconclusive"


@pytest.mark.parametrize("field", ["adaptive_downgrades", "total_freeze_s", "max_freeze_s"])
def test_initial_cumulative_truth_and_counter_resets_are_inconclusive(field):
    samples = full_hd_fixture()
    for sample in samples:
        sample[field] = 1
    assert evaluate_run(12, samples, "turn_tls")["status"] == "inconclusive"
    samples = full_hd_fixture()
    samples[300][field] = 1
    assert evaluate_run(12, samples, "turn_tls")["status"] == "inconclusive"


@pytest.mark.parametrize("capacity", [None, 0, -1, float("nan"), True, "12"])
def test_capacity_must_be_a_positive_independent_measurement(capacity):
    assert evaluate_run(capacity, full_hd_fixture(), "turn_tls")["status"] == "inconclusive"


def test_measured_low_capacity_and_short_duration_fail():
    assert evaluate_run(11.99, full_hd_fixture(), "turn_tls")["status"] == "fail"
    assert evaluate_run(12, full_hd_fixture()[:-1], "turn_tls")["status"] == "fail"


@pytest.mark.parametrize("expected,route,protocol", [
    ("direct", "direct", "unknown"), ("turn_udp", "relay", "udp"),
    ("turn_tcp", "relay", "tcp"), ("turn_tls", "relay", "tls"),
])
@pytest.mark.parametrize("family", ["IPv4", "IPv6", "mixed"])
def test_expected_route_matches_actual_selected_evidence(expected, route, protocol, family):
    samples = full_hd_fixture()
    for sample in samples:
        sample.update(route=route, relay_protocol=protocol, address_family=family)
    assert evaluate_run(12, samples, expected)["status"] == "pass"


@pytest.mark.parametrize("field", ["route", "address_family", "relay_protocol"])
def test_unknown_selected_evidence_does_not_prove_a_route(field):
    samples = full_hd_fixture()
    samples[300][field] = "unknown"
    assert evaluate_run(12, samples, "turn_tls")["status"] == "inconclusive"


def test_receive_bitrate_has_no_motion_floor_and_can_be_null():
    samples = full_hd_fixture()
    for sample in samples:
        sample["bitrate_mbps"] = None
    assert evaluate_run(12, samples, "turn_tls")["status"] == "pass"
    for sample in samples:
        sample["bitrate_mbps"] = 0
    assert evaluate_run(12, samples, "turn_tls")["status"] == "pass"


@pytest.mark.parametrize("samples", [[], None, {}, [None]])
def test_no_usable_samples_are_inconclusive(samples):
    assert evaluate_run(12, samples, "turn_tls")["status"] == "inconclusive"


def run_cli(tmp_path, evidence):
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence))
    return subprocess.run([sys.executable, str(ROOT / "scripts/validate_remote_stream.py"), str(path)], capture_output=True, text=True)


def run_fixture():
    return {"capacity_mbps": 12, "expected_route": "turn_tls", "target_bitrate_mbps": 8, "samples": full_hd_fixture()}


@pytest.mark.parametrize("status,exit_code", [("pass", 0), ("fail", 1), ("inconclusive", 2)])
def test_cli_exit_codes_and_json_results(tmp_path, status, exit_code):
    evidence = run_fixture()
    if status == "fail":
        evidence["samples"][300]["decoded_fps"] = 0
    elif status == "inconclusive":
        evidence["samples"][300]["decoded_fps"] = None
    result = run_cli(tmp_path, evidence)
    assert result.returncode == exit_code
    assert json.loads(result.stdout)["status"] == status
    assert result.stderr == ""


@pytest.mark.parametrize("mutation", ["conflicting_target", "missing_target", "null_target", "unknown_route", "non_object"])
def test_cli_invalid_root_contract_is_inconclusive(tmp_path, mutation):
    evidence = run_fixture()
    if mutation == "conflicting_target":
        evidence["samples"][300]["target_bitrate_mbps"] = 1.7
    elif mutation == "missing_target":
        del evidence["target_bitrate_mbps"]
    elif mutation == "null_target":
        evidence["target_bitrate_mbps"] = None
    elif mutation == "unknown_route":
        evidence["expected_route"] = "unknown"
    else:
        evidence = []
    result = run_cli(tmp_path, evidence)
    assert result.returncode == 2
    assert json.loads(result.stdout)["status"] == "inconclusive"


def test_cli_never_echoes_private_values_or_file_errors(tmp_path):
    secret = "credential=private-secret 192.0.2.123 v=0 SDP"
    evidence = run_fixture()
    evidence["expected_route"] = secret
    evidence["samples"][300]["route"] = secret
    evidence["token"] = secret
    result = run_cli(tmp_path, evidence)
    assert result.returncode == 2
    assert "private-secret" not in result.stdout + result.stderr
    assert "192.0.2.123" not in result.stdout + result.stderr
    for argument in [[], [str(tmp_path / secret)]]:
        result = subprocess.run([sys.executable, str(ROOT / "scripts/validate_remote_stream.py"), *argument], capture_output=True, text=True)
        assert result.returncode == 2
        assert json.loads(result.stdout)["status"] == "inconclusive"
        assert "private-secret" not in result.stdout + result.stderr
    path = tmp_path / "malformed.json"
    path.write_text(secret)
    result = subprocess.run([sys.executable, str(ROOT / "scripts/validate_remote_stream.py"), str(path)], capture_output=True, text=True)
    assert result.returncode == 2
    assert "private-secret" not in result.stdout + result.stderr
