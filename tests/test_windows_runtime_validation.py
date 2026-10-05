"""Build-time version gate; actual installer decision runs on Windows CI."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("runtime,compiler,want", [
    ("14.44.35211.0", "14.51.36231", 1),
    ("14.51.36247.0", "14.51.36231", 0),
    ("14.51.36231.0", "14.51.36231", 0),
    ("14.51.36230.99", "14.51.36231", 1),
    ("14.52.1.0", "14.51.36231", 0),
    ("garbage", "14.51.36231", 1),
    ("14.51.36247.0", "", 1),
])
def test_gate_rejects_stale_or_unknown_runtime_without_packaging(tmp_path, runtime, compiler, want):
    report = tmp_path / "runtime-version.json"
    result = subprocess.run([
        sys.executable, str(ROOT / "scripts/validate_windows_runtime.py"),
        "--runtime-version", runtime, "--compiler-version", compiler,
        "--output", str(report),
    ], capture_output=True, text=True)
    assert result.returncode == want, result.stderr
    recorded = json.loads(report.read_text())
    assert recorded["status"] == ("pass" if want == 0 else "fail")
    assert recorded["runtime_version"] == runtime
    assert recorded["compiler_version"] == compiler
