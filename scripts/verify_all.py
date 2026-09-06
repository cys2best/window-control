#!/usr/bin/env python3
"""Unified Automated Monorepo Verifier for WindowControl (v3.1.0).

Runs all automated backend, frontend, desktop, and signaling checks in a single command
with zero manual prompts, proving system integrity before any manual hardware checks.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import time

REPO_ROOT = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass


def _run_step(name: str, cmd: list[str], cwd: Path | None = None, env: dict[str, str] | None = None) -> tuple[bool, str, float]:
    start = time.time()
    work_dir = cwd or REPO_ROOT
    use_shell = sys.platform == "win32"
    run_env = dict(os.environ)
    if env:
        run_env.update(env)
    try:
        proc = subprocess.run(
            cmd,
            cwd=work_dir,
            capture_output=True,
            text=True,
            check=False,
            shell=use_shell,
            env=run_env,
        )
        duration = time.time() - start
        output = proc.stdout + proc.stderr
        return proc.returncode == 0, output, duration
    except Exception as e:
        duration = time.time() - start
        return False, str(e), duration


def main() -> int:
    print("=" * 72)
    print(" WINDOWCONTROL v3.1.0 — ZERO-CONFIG MONOREPO AUTOMATED VERIFICATION")
    print("=" * 72)

    steps = [
        (
            "Python Backend Suites & Desktop Host",
            ["uv", "run", "pytest", "tests/", "apps/desktop/", "-q"],
            REPO_ROOT,
        ),
        (
            "Host Launcher Widget Headless Tests",
            ["uv", "run", "pytest", "tests/test_launcher_widget.py", "-q"],
            REPO_ROOT,
        ),
        (
            "TypeScript Core Session & Signaling",
            ["npm", "run", "test:core"],
            REPO_ROOT,
        ),
        (
            "TypeScript Shared UI Presentation",
            ["npm", "run", "test:ui"],
            REPO_ROOT,
        ),
        (
            "Web Client Route & Redirection Tests",
            ["npm", "test", "-w", "apps/web"],
            REPO_ROOT,
        ),
        (
            "Web Client Static Export Build",
            ["npm", "run", "build", "-w", "apps/web"],
            REPO_ROOT,
        ),
        (
            "VPS WebRTC Signaling Relay Tests",
            ["npm", "test"],
            REPO_ROOT / "infra" / "vps" / "signaling",
        ),
    ]

    import shutil

    results = []
    overall_pass = True

    pytest_env = {"SUPABASE_URL": "", "AUTH_TOKEN": ""}

    for title, cmd, cwd in steps:
        sys.stdout.write(f"[*] Running {title}... ")
        sys.stdout.flush()
        if "Build" in title:
            shutil.rmtree(REPO_ROOT / "apps" / "web" / "out", ignore_errors=True)
        step_env = pytest_env if "pytest" in cmd else None
        ok, out, duration = _run_step(title, cmd, cwd, env=step_env)
        if ok:
            print(f"PASS ({duration:.2f}s)")
            results.append((title, "PASS", duration, ""))
        else:
            print(f"FAIL ({duration:.2f}s)")
            results.append((title, "FAIL", duration, out))
            overall_pass = False

    # Step: Verify Web Static Export Artifact Integrity
    sys.stdout.write("[*] Verifying Web Export Artifact Integrity... ")
    sys.stdout.flush()
    start = time.time()
    out_dir = REPO_ROOT / "apps" / "web" / "out"
    required_pages = ["index.html", "login.html", "instances.html", "stream.html", "404.html"]
    missing = [p for p in required_pages if not (out_dir / p).exists()]
    unwanted = []
    if (out_dir / "setup.html").exists():
        unwanted.append("setup.html (retired route should not exist)")
    duration = time.time() - start
    if not missing and not unwanted:
        print(f"PASS ({duration:.2f}s)")
        results.append(("Web Export Artifact Integrity", "PASS", duration, ""))
    else:
        err = f"Missing: {missing}, Unwanted: {unwanted}"
        print(f"FAIL ({duration:.2f}s) - {err}")
        results.append(("Web Export Artifact Integrity", "FAIL", duration, err))
        overall_pass = False

    # Summary
    print("\n" + "=" * 72)
    print(" VERIFICATION SUMMARY")
    print("=" * 72)
    for title, status, dur, out in results:
        status_colored = f"[{status}]"
        print(f" {status_colored:<8} {title:<48} ({dur:.2f}s)")
        if status == "FAIL" and out:
            lines = out.strip().splitlines()
            for l in lines[-8:]:
                print(f"          | {l}")

    print("-" * 72)
    if overall_pass:
        print(f" ALL {len(results)} AUTOMATED GATES PASSED (0 failures)")
        print(" Proceed to physical hardware checks (Option B host GUI, device touch).")
        print("=" * 72)
        return 0
    else:
        print(" ONE OR MORE AUTOMATED GATES FAILED. See details above.")
        print("=" * 72)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
