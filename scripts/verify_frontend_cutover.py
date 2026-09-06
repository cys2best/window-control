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

from scripts.verify_lib import OwnedProcess, _pid_started_at
import httpx

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


GATE_NAMES: tuple[str, ...] = (
    "dev_app_health",
    "web_routes",
    "rsc_payloads",
    "instances_negotiation",
    "auth_gate",
    "offline_suites",
    "installed_app_launch",
    "frozen_package_layout",
    "desktop_shell_visual",
    "supabase_two_account_flow",
    "leaked_key_forgery_check",
)

GATE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "web_routes": ("dev_app_health",),
    "rsc_payloads": ("dev_app_health",),
    "instances_negotiation": ("dev_app_health",),
    "auth_gate": ("dev_app_health",),
    "frozen_package_layout": ("installed_app_launch",),
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


_RSC_PAGES = ("index", "login", "instances", "stream")


def _install_dir(installer_path: Path) -> Path:
    r"""Real installs from build/installer.iss land at
    C:\Program Files\WindowControl regardless of where the .exe installer
    itself sits on disk (installer_path points at the installer artifact,
    not the install destination) -- ArchitecturesInstallIn64BitMode makes
    {autopf} resolve to Program Files, not Program Files (x86).
    """
    import os

    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    return Path(program_files) / "WindowControl"


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
        use_shell = sys.platform == "win32"
        try:
            completed = subprocess.run(
                command,
                cwd=cwd or self.config.repo_root,
                text=True,
                capture_output=True,
                timeout=timeout,
                shell=use_shell,
                encoding="utf-8",
                errors="replace",
                **no_window,
            )
        except subprocess.TimeoutExpired as error:
            return 1, f"timed out after {timeout}s: {error}"
        except OSError as error:
            return 1, f"command failed to start: {error}"
        return completed.returncode, (completed.stdout or "") + (completed.stderr or "")

    def start_installed_app(self) -> OwnedProcess:
        install_dir = _install_dir(self.config.installer_path)
        no_window = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
        process = subprocess.Popen(
            [str(install_dir / "WindowControl.exe")],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **no_window,
        )
        import psutil

        started_at = psutil.Process(process.pid).create_time()
        self._owned_installed_app = OwnedProcess("installed_app", process.pid, started_at)
        return self._owned_installed_app

    def start_selfrelaunch(self, url: str) -> OwnedProcess:
        install_dir = _install_dir(self.config.installer_path)
        no_window = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
        process = subprocess.Popen(
            [str(install_dir / "WindowControl.exe"), "--webview-window", url],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **no_window,
        )
        import psutil

        started_at = psutil.Process(process.pid).create_time()
        return OwnedProcess("selfrelaunch", process.pid, started_at)

    def process_is_alive(self, process: OwnedProcess) -> bool:
        return _pid_started_at(process.pid) == process.started_at

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def manual_confirm(self, message: str, checkpoint: str) -> str:
        if self.config.file_prompts:
            from scripts.verify_lib import CutoverFilePromptChannel
            channel = CutoverFilePromptChannel(self.config.evidence_dir, poll_seconds=self.config.file_prompt_poll_seconds)
            return channel.prompt(message, checkpoint)
        print(f"CHECKPOINT: {checkpoint}\n{message}")
        answer = input("PASS/FAIL (or SKIP where offered)? ").strip().upper()
        return answer



def gate_dev_app_health(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    environment = dict(os.environ)
    environment.pop("AUTH_TOKEN", None)
    app = deps.start_dev_app(environment)
    if deps.wait_for_dev_app(app, config.port):
        result.mark("dev_app_health", "PASS", reason=reason, details={"pid": app.pid, "started_at": app.started_at})
    else:
        result.mark("dev_app_health", "FAIL", reason=reason, details={"error": "dev app did not become healthy within the timeout"})


def gate_web_routes(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    observed: dict[str, Any] = {}
    failures: list[str] = []
    for path in ("/", "/login", "/instances", "/stream"):
        headers = {"Accept": "text/html"} if path == "/instances" else None
        status, content_type, _ = deps.get(config.port, path, headers=headers)
        observed[path] = {"status": status, "content_type": content_type}
        if status != 200 or "text/html" not in content_type:
            failures.append(f"{path}: expected 200 text/html, got {status} {content_type!r}")
    setup_status, setup_ct, _ = deps.get(config.port, "/setup")
    observed["/setup"] = {"status": setup_status, "content_type": setup_ct}
    if setup_status == 200:
        failures.append("/setup: retired route should not return 200")
    if failures:
        result.mark("web_routes", "FAIL", reason=reason, details={"observed": observed, "failures": failures})
    else:
        result.mark("web_routes", "PASS", reason=reason, details={"observed": observed})


_RSC_EXPECTED_CONTENT_TYPES = {
    "manifest.json": "json",
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
    if no_header[0] not in (200, 401) or "application/json" not in no_header[1]:
        failures.append("no-Accept-header request did not return 200/401 JSON")
    if json_header[0] not in (200, 401) or "application/json" not in json_header[1]:
        failures.append("Accept: application/json request did not return 200/401 JSON")
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


_PYTEST_CATEGORY_PATTERN = re.compile(r"\b(\d+) (passed|failed|skipped|errors?)\b")


def _parse_pytest_summary(output: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    for number, category in _PYTEST_CATEGORY_PATTERN.findall(output):
        key = "errors" if category.startswith("error") else category
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
        errors = counts.get("errors", 0)
        failed_lines = [line for line in output.splitlines() if line.startswith("FAILED ") or line.startswith("FAIL ")]
        details[name] = {"exit_code": exit_code, "counts": counts}
        if failed_lines:
            details[name]["failed_tests"] = failed_lines
        if exit_code != 0 or failed != 0 or errors != 0:
            any_failed = True
    if any_failed:
        result.mark("offline_suites", "FAIL", reason=reason, details=details)
    else:
        result.mark("offline_suites", "PASS", reason=reason, details=details)


def gate_installed_app_launch(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    app = deps.start_installed_app()
    healthy = deps.wait_for_dev_app(app, config.port)
    exit_code, output = deps.run_command(
        ["netsh", "advfirewall", "firewall", "show", "rule", 'name="WindowControl-Engine"'],
    )
    expected_engine_path = r"WindowControl\_internal\assets\engine\engine.exe"
    firewall_ok = exit_code == 0 and expected_engine_path.lower() in output.lower()
    details = {
        "pid": app.pid,
        "started_at": app.started_at,
        "healthy": healthy,
        "firewall_output": output[:2000],
        "firewall_ok": firewall_ok,
    }
    if healthy and firewall_ok:
        result.mark("installed_app_launch", "PASS", reason=reason, details=details)
    else:
        result.mark("installed_app_launch", "FAIL", reason=reason, details=details)


def gate_frozen_package_layout(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str, *, parent: OwnedProcess) -> None:
    parent_still_healthy = deps.wait_for_dev_app(parent, config.port)
    route_failures: list[str] = []
    for path in ("/", "/login", "/instances", "/stream"):
        status, content_type, _ = deps.get(config.port, path)
        if status != 200 or "text/html" not in content_type:
            route_failures.append(f"{path}: expected 200, got {status}")
    setup_status, _, _ = deps.get(config.port, "/setup")
    if setup_status == 200:
        route_failures.append("/setup: retired route returned 200")
    details = {
        "parent_pid": parent.pid,
        "parent_still_healthy": parent_still_healthy,
        "route_failures": route_failures,
    }
    if parent_still_healthy and not route_failures:
        result.mark("frozen_package_layout", "PASS", reason=reason, details=details)
    else:
        result.mark("frozen_package_layout", "FAIL", reason=reason, details=details)


def gate_desktop_shell_visual(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    answer = deps.manual_confirm(
        "On the machine running the installed app, click the system tray icon's "
        "'Show' option. Confirm: the Option B Minimal Host Monitor widget opens "
        "(~400px compact card showing server health dot, port 8080, LAN/Tailscale "
        "IPs, relay status, and active streams count), clicking 'Minimize to Tray' "
        "hides the window, and closing the window via [X] minimizes to tray without "
        "terminating the server process.",
        "desktop_shell_visual",
    )
    result.mark("desktop_shell_visual", "PASS" if answer == "PASS" else "FAIL", reason=reason)


def gate_supabase_two_account_flow(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    answer = deps.manual_confirm(
        "Complete the full flow from docs/WINDOWS_MANUAL_VALIDATION.md section 3: "
        "register a new account, confirm empty instance list, confirm the device "
        "claims on first login, confirm a second account cannot see or act on the "
        "first account's claimed instance (403, not silent adoption), confirm "
        "mobile login shows the same linked list as web. PASS only if every part "
        "of this passed.",
        "supabase_two_account_flow",
    )
    result.mark("supabase_two_account_flow", "PASS" if answer == "PASS" else "FAIL", reason=reason)


def gate_leaked_key_forgery_check(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    answer = deps.manual_confirm(
        "If you have a second machine available, complete "
        "docs/WINDOWS_MANUAL_VALIDATION.md section 8 (copy the install's private "
        "key to a second machine, confirm Account B cannot use it to access "
        "Account A's session). If you don't have a second machine for this run, "
        "answer SKIP.",
        "leaked_key_forgery_check",
    )
    if answer == "SKIP":
        result.mark("leaked_key_forgery_check", "SKIPPED", reason=reason, details={"reason": "no second machine available"})
    else:
        result.mark("leaked_key_forgery_check", "PASS" if answer == "PASS" else "FAIL", reason=reason)


_GATE_FUNCTIONS = {
    "dev_app_health": gate_dev_app_health,
    "web_routes": gate_web_routes,
    "rsc_payloads": gate_rsc_payloads,
    "instances_negotiation": gate_instances_negotiation,
    "auth_gate": gate_auth_gate,
    "offline_suites": gate_offline_suites,
    "installed_app_launch": gate_installed_app_launch,
    "desktop_shell_visual": gate_desktop_shell_visual,
    "supabase_two_account_flow": gate_supabase_two_account_flow,
    "leaked_key_forgery_check": gate_leaked_key_forgery_check,
}


def run(config: FrontendCutoverConfig, deps: Any) -> FrontendCutoverResult:
    selection = resolve_gate_selection(config)
    result = FrontendCutoverResult()
    started_app: OwnedProcess | None = None
    started_installed_app: OwnedProcess | None = None
    try:
        for name in GATE_NAMES:
            reason = selection[name]
            if reason.startswith("not selected"):
                result.mark(name, "SKIPPED", reason=reason)
                continue
            if name in ("installed_app_launch", "frozen_package_layout", "leaked_key_forgery_check") and config.skip_installer:
                result.mark(name, "SKIPPED", reason="skipped (--skip-installer)")
                continue
            if name in ("desktop_shell_visual", "supabase_two_account_flow", "leaked_key_forgery_check") and config.skip_manual_gates:
                result.mark(name, "SKIPPED", reason="skipped (--skip-manual-gates)")
                continue
            if name == "dev_app_health":
                gate_fn = getattr(sys.modules[__name__], f"gate_{name}", gate_dev_app_health)
                gate_fn(config, deps, result, reason)
                if result.gates[name]["status"] == "PASS":
                    details = result.gates[name].get("details", {})
                    pid = details.get("pid")
                    if pid is not None:
                        started_at = details.get("started_at")
                        if started_at is None:
                            started_at = _pid_started_at(pid) or 0
                        started_app = OwnedProcess("dev_app", pid, started_at)
            elif name == "frozen_package_layout":
                gate_fn = getattr(sys.modules[__name__], f"gate_{name}", gate_frozen_package_layout)
                import inspect
                sig = inspect.signature(gate_fn)
                if "parent" in sig.parameters:
                    if started_installed_app is not None:
                        gate_fn(config, deps, result, reason, parent=started_installed_app)
                    else:
                        result.mark(name, "FAIL", reason=reason, details={"error": "installed_app_launch did not produce a running app to verify layout against"})
                else:
                    gate_fn(config, deps, result, reason)
            elif name == "installed_app_launch":
                if started_app is not None:
                    terminate = getattr(deps, "terminate", None)
                    if terminate is not None:
                        terminate(started_app)
                    started_app = None
                gate_fn = getattr(sys.modules[__name__], f"gate_{name}", gate_installed_app_launch)
                gate_fn(config, deps, result, reason)
                if result.gates[name]["status"] == "PASS":
                    details = result.gates[name].get("details", {})
                    pid = details.get("pid")
                    if pid is not None:
                        started_at = details.get("started_at")
                        if started_at is None:
                            started_at = _pid_started_at(pid) or 0
                        started_installed_app = OwnedProcess("installed_app", pid, started_at)
            else:
                gate_fn = getattr(sys.modules[__name__], f"gate_{name}", _GATE_FUNCTIONS.get(name))
                gate_fn(config, deps, result, reason)
    finally:
        if not config.keep_on_failure:
            terminate = getattr(deps, "terminate", None)
            if terminate is not None:
                if started_app is not None:
                    terminate(started_app)
                if started_installed_app is not None:
                    terminate(started_installed_app)
    payload_extra: dict[str, Any] = {}
    if config.only is not None:
        payload_extra["selection"] = {"mode": "only", "requested": list(config.only)}
    elif config.from_gate is not None:
        payload_extra["selection"] = {"mode": "from", "requested": [config.from_gate]}
    result.selection_extra = payload_extra  # type: ignore[attr-defined]
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    from scripts.verify_lib import submit_file_confirmation

    parser = argparse.ArgumentParser(description="Verify the frontend/desktop cutover surface")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--web-build-dir", type=Path)
    parser.add_argument("--installer-path", type=Path)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--file-prompts", action="store_true")
    parser.add_argument("--confirm", choices=("PASS", "FAIL"))
    parser.add_argument("--keep-on-failure", action="store_true")
    parser.add_argument("--skip-manual-gates", action="store_true")
    parser.add_argument("--skip-installer", action="store_true")
    parser.add_argument("--only")
    parser.add_argument("--from-gate", "--from", dest="from_gate")
    args = parser.parse_args(argv)

    if args.confirm:
        try:
            path = submit_file_confirmation(args.repo_root, args.confirm, evidence_glob="frontend-cutover-*")
        except Exception as error:  # noqa: BLE001 - mirrors verify_engine_cutover.py's own top-level catch
            print(f"FAIL: {error}")
            return 1
        print(f"Submitted {args.confirm} confirmation to {path.parent.name}")
        return 0

    if not args.evidence_dir or not args.installer_path:
        parser.error("verification requires --evidence-dir and --installer-path")

    config = FrontendCutoverConfig(
        repo_root=args.repo_root,
        evidence_dir=args.evidence_dir,
        web_build_dir=args.web_build_dir or (args.repo_root / "apps" / "web" / "out"),
        installer_path=args.installer_path,
        port=args.port,
        file_prompts=args.file_prompts,
        keep_on_failure=args.keep_on_failure,
        skip_manual_gates=args.skip_manual_gates,
        skip_installer=args.skip_installer,
        only=tuple(args.only.split(",")) if args.only else None,
        from_gate=args.from_gate,
    )
    deps = RealFrontendDeps(config)
    try:
        result = run(config, deps)
    except (FrontendCutoverError, ValueError) as error:
        print(f"FAIL: {error}")
        return 1
    payload = result.to_dict()
    payload.update(getattr(result, "selection_extra", {}))
    from scripts.verify_lib import _write_json_atomic

    _write_json_atomic(config.evidence_dir / "result.json", payload)
    print(json.dumps(payload, indent=2))
    return 0 if result.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())




