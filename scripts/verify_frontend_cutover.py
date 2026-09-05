"""Dependency-injected verifier for the frontend/desktop cutover surface
that scripts/verify_engine_cutover.py doesn't cover: apps/web's static
export routing, the apps/desktop pywebview shell, and the packaged
installer's frontend-specific pieces.

See docs/superpowers/specs/2026-09-06-frontend-cutover-verifier-design.md
for the full design this implements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

import httpx

from scripts.verify_lib import OwnedProcess, _pid_started_at


GATE_NAMES: tuple[str, ...] = (
    "dev_app_health",
    "web_routes",
    "rsc_payloads",
    "instances_negotiation",
    "auth_gate",
    "offline_suites",
    "installed_app_launch",
    "frozen_selfrelaunch",
    "desktop_shell_visual",
    "supabase_two_account_flow",
    "leaked_key_forgery_check",
)

GATE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "web_routes": ("dev_app_health",),
    "rsc_payloads": ("dev_app_health",),
    "instances_negotiation": ("dev_app_health",),
    "auth_gate": ("dev_app_health",),
    "frozen_selfrelaunch": ("installed_app_launch",),
}


class FrontendCutoverError(RuntimeError):
    pass


@dataclass
class FrontendCutoverConfig:
    repo_root: Path
    evidence_dir: Path
    web_build_dir: Path
    installer_path: Path
    port: int = 8080
    file_prompts: bool = False
    file_prompt_poll_seconds: float = 0.25
    keep_on_failure: bool = False
    skip_manual_gates: bool = False
    skip_installer: bool = False
    only: tuple[str, ...] | None = None
    from_gate: str | None = None


@dataclass
class FrontendCutoverResult:
    status: str = "PASS"
    gates: dict[str, dict[str, Any]] = field(default_factory=dict)

    def mark(
        self,
        name: str,
        status: str,
        *,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.gates[name] = {"status": status, "reason": reason, "details": details or {}}
        if status == "FAIL":
            self.status = "FAIL"
        elif status == "SKIPPED" and self.status == "PASS":
            self.status = "INCOMPLETE"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": self.status, "gates": self.gates}
        return payload


def resolve_gate_selection(config: FrontendCutoverConfig) -> dict[str, str]:
    """Pure function: compute {gate_name: reason} for every gate in GATE_NAMES.

    No I/O, no side effects — this is the scheduling/dependency-closure
    logic in isolation, testable without any real gate implementation.
    """
    if config.only is not None and config.from_gate is not None:
        raise ValueError("--only and --from are mutually exclusive")

    for name in config.only or ():
        if name not in GATE_NAMES:
            raise ValueError(f"unknown gate {name!r}")
    if config.from_gate is not None and config.from_gate not in GATE_NAMES:
        raise ValueError(f"unknown gate {config.from_gate!r}")

    if config.only is None and config.from_gate is None:
        return {name: "requested" for name in GATE_NAMES}

    requested: set[str]
    not_selected_reason: str
    if config.only is not None:
        requested = set(config.only)
        not_selected_reason = "not selected this run (--only)"
    else:
        index = GATE_NAMES.index(config.from_gate)  # type: ignore[arg-type]
        requested = set(GATE_NAMES[index:])
        not_selected_reason = "not selected this run (--from)"

    # Compute the dependency closure: anything a requested gate needs,
    # transitively, that wasn't itself requested. Deterministic order
    # based on GATE_NAMES so earlier pipeline gates take precedence.
    needed: dict[str, str] = {}
    queue = [name for name in GATE_NAMES if name in requested]
    while queue:
        name = queue.pop(0)
        for dependency in GATE_DEPENDENCIES.get(name, ()):
            if dependency not in requested and dependency not in needed:
                needed[dependency] = f"auto-included as prerequisite of {name}"
                queue.append(dependency)

    selection: dict[str, str] = {}
    for name in GATE_NAMES:
        if name in requested:
            selection[name] = "requested"
        elif name in needed:
            selection[name] = needed[name]
        else:
            selection[name] = not_selected_reason
    return selection


_RSC_PAGES = ("index", "login", "setup", "instances", "stream")


class RealFrontendDeps:
    def __init__(self, config: FrontendCutoverConfig):
        self.config = config
        self._owned_dev_app: OwnedProcess | None = None
        self._owned_installed_app: OwnedProcess | None = None

    def start_dev_app(self, environment: dict[str, str]) -> OwnedProcess:
        self.config.evidence_dir.mkdir(parents=True, exist_ok=True)
        app_log = open(self.config.evidence_dir / "dev-app.log", "a", encoding="utf-8")
        no_window = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
        try:
            process = subprocess.Popen(
                ["uv", "run", "python", "src/main.py"],
                cwd=self.config.repo_root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=app_log,
                stderr=subprocess.STDOUT,
                **no_window,
            )
        finally:
            app_log.close()
        import psutil

        started_at = psutil.Process(process.pid).create_time()
        self._owned_dev_app = OwnedProcess("dev_app", process.pid, started_at)
        return self._owned_dev_app

    def wait_for_dev_app(self, app: OwnedProcess, port: int) -> bool:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if _pid_started_at(app.pid) != app.started_at:
                return False
            try:
                response = httpx.get(f"http://127.0.0.1:{port}/auth/config", timeout=2)
                if response.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        return False

    def auth_config(self, port: int) -> dict[str, Any]:
        response = httpx.get(f"http://127.0.0.1:{port}/auth/config", timeout=5)
        response.raise_for_status()
        return response.json()

    def get(self, port: int, path: str, *, headers: dict[str, str] | None = None) -> tuple[int, str, str]:
        response = httpx.get(f"http://127.0.0.1:{port}{path}", headers=headers or {}, timeout=10)
        return response.status_code, response.headers.get("content-type", ""), response.text

    def terminate(self, process: OwnedProcess) -> None:
        if _pid_started_at(process.pid) != process.started_at:
            return  # already gone, or PID reused by something else
        import psutil

        try:
            psutil.Process(process.pid).terminate()
        except psutil.Error:
            pass

    def run_command(self, command: list[str], *, cwd: Path | None = None, timeout: float = 600) -> tuple[int, str]:
        no_window = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
        try:
            completed = subprocess.run(
                command,
                cwd=cwd or self.config.repo_root,
                text=True,
                capture_output=True,
                timeout=timeout,
                **no_window,
            )
        except subprocess.TimeoutExpired as error:
            return 1, f"timed out after {timeout}s: {error}"
        return completed.returncode, completed.stdout + completed.stderr


def gate_dev_app_health(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    environment = dict(os.environ)
    environment.pop("AUTH_TOKEN", None)
    app = deps.start_dev_app(environment)
    if deps.wait_for_dev_app(app, config.port):
        result.mark("dev_app_health", "PASS", reason=reason, details={"pid": app.pid})
    else:
        result.mark("dev_app_health", "FAIL", reason=reason, details={"error": "dev app did not become healthy within the timeout"})


def gate_web_routes(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    observed: dict[str, Any] = {}
    failures: list[str] = []
    for path in ("/", "/login", "/setup", "/stream"):
        status, content_type, _ = deps.get(config.port, path)
        observed[path] = {"status": status, "content_type": content_type}
        if status != 200 or "text/html" not in content_type:
            failures.append(f"{path}: expected 200 text/html, got {status} {content_type!r}")
    if failures:
        result.mark("web_routes", "FAIL", reason=reason, details={"observed": observed, "failures": failures})
    else:
        result.mark("web_routes", "PASS", reason=reason, details={"observed": observed})


_RSC_EXPECTED_CONTENT_TYPES = {
    "manifest.json": "application/json",
    "icon-192.png": "image/png",
    "404.html": "text/html",
}


def gate_rsc_payloads(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    observed: dict[str, Any] = {}
    failures: list[str] = []
    paths = [f"/{page}.txt" for page in _RSC_PAGES] + ["/404.html", "/manifest.json", "/icon-192.png"]
    for path in paths:
        status, content_type, _ = deps.get(config.port, path)
        observed[path] = {"status": status, "content_type": content_type}
        name = path.lstrip("/")
        expected = _RSC_EXPECTED_CONTENT_TYPES.get(name, "text/x-component")
        if status != 200 or expected not in content_type:
            failures.append(f"{path}: expected 200 {expected!r}, got {status} {content_type!r}")
    if failures:
        result.mark("rsc_payloads", "FAIL", reason=reason, details={"observed": observed, "failures": failures})
    else:
        result.mark("rsc_payloads", "PASS", reason=reason, details={"observed": observed})


def gate_instances_negotiation(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    no_header = deps.get(config.port, "/instances")
    json_header = deps.get(config.port, "/instances", headers={"Accept": "application/json"})
    html_header = deps.get(config.port, "/instances", headers={"Accept": "text/html"})
    observed = {
        "no_accept_header": {"status": no_header[0], "content_type": no_header[1]},
        "accept_application_json": {"status": json_header[0], "content_type": json_header[1]},
        "accept_text_html": {"status": html_header[0], "content_type": html_header[1]},
    }
    failures: list[str] = []
    if "application/json" not in no_header[1]:
        failures.append("no-Accept-header request did not return JSON")
    if "application/json" not in json_header[1]:
        failures.append("Accept: application/json request did not return JSON")
    if html_header[0] != 200 or "text/html" not in html_header[1]:
        failures.append("Accept: text/html request did not return the HTML page shell")
    if failures:
        result.mark("instances_negotiation", "FAIL", reason=reason, details={"observed": observed, "failures": failures})
    else:
        result.mark("instances_negotiation", "PASS", reason=reason, details={"observed": observed})


def gate_auth_gate(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    auth_config = deps.auth_config(config.port)
    if not auth_config.get("auth_enabled"):
        result.mark(
            "auth_gate", "SKIPPED", reason=reason,
            details={"reason": "Supabase auth not configured this run"},
        )
        return
    no_token = deps.get(config.port, "/instances")
    bad_token = deps.get(config.port, "/instances", headers={"Authorization": "Bearer not-a-real-token"})
    observed = {"no_token_status": no_token[0], "bad_token_status": bad_token[0]}
    if no_token[0] == 401 and bad_token[0] == 401:
        result.mark("auth_gate", "PASS", reason=reason, details={"observed": observed})
    else:
        result.mark("auth_gate", "FAIL", reason=reason, details={"observed": observed, "error": "an unauthenticated or garbage-token request to /instances did not get 401"})


_PYTEST_CATEGORY_PATTERN = re.compile(r"(\d+) (passed|failed|skipped|error)")


def _parse_pytest_summary(output: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    for number, category in _PYTEST_CATEGORY_PATTERN.findall(output):
        key = "errors" if category == "error" else category
        counts[key] = int(number)
    return counts


def _parse_jest_summary(output: str) -> dict[str, int]:
    match = re.search(r"^Tests:\s+(.*)$", output, re.MULTILINE)
    line = match.group(1) if match else ""
    passed_match = re.search(r"(\d+) passed", line)
    failed_match = re.search(r"(\d+) failed", line)
    total_match = re.search(r"(\d+) total", line)
    return {
        "passed": int(passed_match.group(1)) if passed_match else 0,
        "failed": int(failed_match.group(1)) if failed_match else 0,
        "total": int(total_match.group(1)) if total_match else 0,
    }


_SUITE_COMMANDS: tuple[tuple[str, list[str], str], ...] = (
    ("pytest_tests", ["uv", "run", "pytest", "tests/", "-q", "--continue-on-collection-errors"], "pytest"),
    ("pytest_apps_desktop", ["uv", "run", "pytest", "apps/desktop/", "-q"], "pytest"),
    ("jest_core", ["npm", "run", "test:core"], "jest"),
    ("jest_ui", ["npm", "run", "test:ui"], "jest"),
    ("jest_web", ["npm", "test", "-w", "apps/web"], "jest"),
)


def gate_offline_suites(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    details: dict[str, Any] = {}
    any_failed = False
    for name, command, kind in _SUITE_COMMANDS:
        exit_code, output = deps.run_command(command, cwd=config.repo_root, timeout=600)
        counts = _parse_pytest_summary(output) if kind == "pytest" else _parse_jest_summary(output)
        failed = counts.get("failed", 0)
        details[name] = {"exit_code": exit_code, "counts": counts}
        if exit_code != 0 or failed != 0:
            any_failed = True
    if any_failed:
        result.mark("offline_suites", "FAIL", reason=reason, details=details)
    else:
        result.mark("offline_suites", "PASS", reason=reason, details=details)


