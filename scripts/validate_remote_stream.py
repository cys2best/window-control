"""Assess sanitized RemoteRunEvidence; synthetic evidence is not device acceptance."""
import json
import math
from pathlib import Path
import sys


EXPECTED_ROUTES = {
    "direct": ("direct", None),
    "turn_udp": ("relay", "udp"),
    "turn_tcp": ("relay", "tcp"),
    "turn_tls": ("relay", "tls"),
}
NUMERIC_FIELDS = (
    "elapsed_s", "source_width", "source_height", "decoded_width",
    "decoded_height", "decoded_fps", "target_bitrate_mbps",
    "adaptive_downgrades", "total_freeze_s", "max_freeze_s",
)
INTEGER_FIELDS = (
    "source_width", "source_height", "decoded_width", "decoded_height",
    "adaptive_downgrades",
)
COUNTERS = ("adaptive_downgrades", "total_freeze_s", "max_freeze_s")


def _number(value):
    return (
        isinstance(value, (int, float)) and not isinstance(value, bool)
        and math.isfinite(value) and value >= 0
    )


def _inconclusive(reasons):
    return {"status": "inconclusive", "reasons": reasons, "metrics": {}}


def evaluate_run(capacity_mbps: float, samples: list[dict], expected_route: str) -> dict:
    """Require complete measured evidence before evaluating acceptance thresholds.

    Invalid/missing observations take precedence over measured failure. Receive
    bitrate is optional diagnostic evidence and cannot establish path capacity.
    """
    if not _number(capacity_mbps) or capacity_mbps <= 0:
        return _inconclusive(["Independent positive capacity measurement is required."])
    if not isinstance(expected_route, str) or expected_route not in EXPECTED_ROUTES:
        return _inconclusive(["A supported expected route is required."])
    if not isinstance(samples, list) or len(samples) < 2:
        return _inconclusive(["At least two measured samples are required."])

    previous = None
    gap = False
    expected_selected_route, expected_protocol = EXPECTED_ROUTES[expected_route]
    for sample in samples:
        if not isinstance(sample, dict):
            return _inconclusive(["Each sample must be a measurement object."])
        for field in NUMERIC_FIELDS:
            if not _number(sample.get(field)):
                return _inconclusive([f"Missing or invalid measurement: {field}."])
        for field in INTEGER_FIELDS:
            if sample[field] % 1 != 0:
                return _inconclusive([f"Invalid integer measurement: {field}."])
        bitrate = sample.get("bitrate_mbps")
        if bitrate is not None and not _number(bitrate):
            return _inconclusive(["Invalid diagnostic receive bitrate."])
        for field, allowed in (
            ("route", ("direct", "relay")),
            ("address_family", ("IPv4", "IPv6", "mixed")),
            ("relay_protocol", ("udp", "tcp", "tls", "unknown")),
        ):
            if not isinstance(sample.get(field), str) or sample[field] not in allowed:
                return _inconclusive([f"Missing or invalid selected evidence: {field}."])
        if sample["route"] == "relay" and sample["relay_protocol"] == "unknown":
            return _inconclusive(["Selected relay protocol is required."])
        if previous is not None:
            interval = sample["elapsed_s"] - previous["elapsed_s"]
            if interval <= 0:
                return _inconclusive(["Sample timestamps must be strictly chronological."])
            # Current exporter uses a one-second sampler, with no cadence field.
            gap |= interval > 2
            if any(sample[field] < previous[field] for field in COUNTERS):
                return _inconclusive(["Cumulative measurements must not reset."])
        previous = sample
    if any(samples[0][field] != 0 for field in COUNTERS):
        return _inconclusive(["Initial cumulative freeze and downgrade measurements must be zero."])
    if gap:
        return _inconclusive(["Observation gap exceeds the current two-second limit."])

    duration_s = samples[-1]["elapsed_s"] - samples[0]["elapsed_s"]
    full_hd = all((s["source_width"], s["source_height"], s["decoded_width"], s["decoded_height"])
                  == (1920, 1080, 1920, 1080) for s in samples)
    fps_ok = all(s["decoded_fps"] >= 30 for s in samples)
    target_ok = all(s["target_bitrate_mbps"] == 8 for s in samples)
    no_downgrades = all(s["adaptive_downgrades"] == 0 for s in samples)
    freeze_total_s = samples[-1]["total_freeze_s"] - samples[0]["total_freeze_s"]
    freeze_ok = max(s["max_freeze_s"] for s in samples) <= 1 and freeze_total_s / duration_s < 0.01
    route_ok = all(
        s["route"] == expected_selected_route
        and (expected_protocol is None or s["relay_protocol"] == expected_protocol)
        for s in samples
    )
    reasons = []
    for accepted, reason in (
        (capacity_mbps >= 12, "Independently measured capacity is below 12 Mbps."),
        (duration_s >= 600, "Measured duration is below 600 seconds."),
        (full_hd, "Source and decoded dimensions must remain 1920x1080."),
        (fps_ok, "Decoded frame rate fell below 30 FPS."),
        (target_ok, "Encoder target must remain 8 Mbps."),
        (no_downgrades, "An adaptive downgrade was measured."),
        (freeze_ok, "Freeze duration exceeds one second or total freeze fraction is at least 1%."),
        (route_ok, "Selected route or relay protocol does not match the expected route."),
    ):
        if not accepted:
            reasons.append(reason)
    bitrates = [s["bitrate_mbps"] for s in samples if s.get("bitrate_mbps") is not None]
    return {
        "status": "fail" if reasons else "pass",
        "reasons": reasons,
        "metrics": {
            "capacity_mbps": capacity_mbps, "duration_s": duration_s,
            "sample_count": len(samples), "min_decoded_fps": min(s["decoded_fps"] for s in samples),
            "total_freeze_s": freeze_total_s, "max_freeze_s": max(s["max_freeze_s"] for s in samples),
            "freeze_fraction": freeze_total_s / duration_s,
            "adaptive_downgrades": samples[-1]["adaptive_downgrades"],
            "min_receive_bitrate_mbps": min(bitrates) if bitrates else None,
            "max_receive_bitrate_mbps": max(bitrates) if bitrates else None,
            "selected_routes": sorted({s["route"] for s in samples}),
            "address_families": sorted({s["address_family"] for s in samples}),
            "relay_protocols": sorted({s["relay_protocol"] for s in samples}),
        },
    }


def _evaluate_export(evidence):
    if not isinstance(evidence, dict):
        return _inconclusive(["Run evidence must be an object."])
    target = evidence.get("target_bitrate_mbps")
    if not _number(target):
        return _inconclusive(["A measured root encoder target is required."])
    samples = evidence.get("samples")
    if isinstance(samples, list) and any(
        isinstance(sample, dict) and sample.get("target_bitrate_mbps") != target
        for sample in samples
    ):
        return _inconclusive(["Root and sample encoder targets conflict."])
    return evaluate_run(evidence.get("capacity_mbps"), samples, evidence.get("expected_route"))


def main():
    if len(sys.argv) != 2:
        result = _inconclusive(["Provide one evidence JSON file."])
    else:
        try:
            evidence = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            result = _inconclusive(["Evidence JSON could not be read or parsed."])
        else:
            result = _evaluate_export(evidence)
    print(json.dumps(result, allow_nan=False))
    return {"pass": 0, "fail": 1, "inconclusive": 2}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(main())
