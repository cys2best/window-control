"""Dependency-injected Windows acceptance runner for engine orchestration.

The command-line PowerShell wrapper intentionally contains no lifecycle policy.
This module owns the test state machine so Darwin tests can exercise every
branch with fakes while Windows runs use the real command, ADB, process, HTTP,
and browser adapters.
"""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import platform
import shutil
import subprocess
import time
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


class VerificationError(RuntimeError):
    pass


@dataclass
class VerificationConfig:
    repo_root: Path
    engine_exe: Path
    evidence_dir: Path
    serial: str | None = None
    tier: str = "720"
    relay_port: int = 8443
    page_port: int = 8090
    app_port: int = 8080
    skip_build: bool = True
    skip_tests: bool = True
    skip_expiry: bool = True
    keep_on_failure: bool = False
    enforce_windows: bool = True
    require_engine_binary: bool = True
    poll_seconds: float = 1.0
    expiry_poll_seconds: float = 30.0
    expiry_grace_seconds: float = 1.0


@dataclass
class VerificationResult:
    status: str = "PASS"
    checkpoints: dict[str, dict[str, str]] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)

    def mark(self, name: str, status: str, detail: str = "") -> None:
        self.checkpoints[name] = {"status": status, "detail": detail}
        if status == "FAIL":
            self.status = "FAIL"
        elif status == "SKIP" and self.status == "PASS":
            self.status = "INCOMPLETE"


_ENV_NAMES = (
    "ENGINE_EXE_PATH",
    "ENGINE_WHEP_CAPABILITY_SECRET",
    "ENGINE_SIGNALING_SECRET",
    "ENGINE_LOCAL_ICE_SERVERS",
    "ENGINE_PUBLIC_ICE_SERVERS",
    "VPS_SIGNALING_URL",
    "AUTH_TOKEN",
    "PUBLIC_UI_URL",
    "TUNNEL_SECRET",
)


def _strip_serial(value: str) -> str:
    return value[4:] if value.startswith("adb:") else value


def _fragment(selection: dict[str, Any]) -> str:
    # Fragments never reach the static server's request line/log. Do not put
    # credentials in the query string or in evidence files.
    payload = {
        "whep_url": selection["whep_url"],
        "whep_token": selection["whep_token"],
        "generation": selection["generation"],
        "width": selection.get("w", selection.get("width")),
        "height": selection.get("h", selection.get("height")),
        "ice_servers": selection.get("ice_servers", []),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode()
    ).decode().rstrip("=")
    return "#" + encoded


def _safe_selection(selection: dict[str, Any]) -> dict[str, Any]:
    return {
        key: selection.get(key)
        for key in ("whep_url", "generation", "w", "h", "width", "height")
        if key in selection
    }


def _loop_until(
    deps: Any,
    predicate: Callable[[], bool],
    *,
    timeout: float,
    description: str,
    interval: float,
) -> None:
    deadline = deps.clock() + timeout
    while not predicate():
        remaining = deadline - deps.clock()
        if remaining <= 0:
            raise VerificationError(f"timed out waiting for {description}")
        deps.sleep(min(interval, remaining))


def _is_loopback(url: str) -> bool:
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname
    try:
        return bool(host and ipaddress.ip_address(host).is_loopback)
    except ValueError:
        return host in {"localhost"}


def _record_command(deps: Any, command: list[str], label: str) -> None:
    # RealDeps writes full command/output to disk; fakes retain the command
    # tuple so behavior tests can assert the boundary without source-grepping.
    deps.record_command(command, label)


def _trace(deps: Any, message: str) -> None:
    recorder = getattr(deps, "record_event", None)
    if recorder is not None:
        recorder(message)


def _ask(deps: Any, result: VerificationResult, name: str, message: str) -> bool:
    answer = deps.prompt(message)
    if answer != "PASS":
        result.mark(name, "FAIL", f"operator response: {answer}")
        return False
    result.mark(name, "PASS", "operator confirmed")
    return True


def run_verification(config: VerificationConfig, deps: Any) -> VerificationResult:
    result = VerificationResult()
    config.evidence_dir.mkdir(parents=True, exist_ok=True)
    if config.enforce_windows and platform.system() != "Windows":
        raise VerificationError("Windows Host PC required; Windows integration is not verified here")

    # Refuse to attach to a process the runner did not create. This happens
    # before build, relay, app, browser, or ADB state changes.
    existing = list(deps.list_engine_processes())
    _trace(deps, f"pre-existing engine count={len(existing)}")
    if existing:
        raise VerificationError("pre-existing engine.exe process found; close it before retrying")

    if config.skip_build is False:
        _record_command(deps, ["cmake", "--build", str(config.repo_root / "engine" / "build"), "--config", "Release"], "engine build")
        deps.run(
            ["cmake", "--build", str(config.repo_root / "engine" / "build"), "--config", "Release"],
            cwd=config.repo_root, env=dict(os.environ), label="engine build",
        )
    else:
        result.mark("engine build", "SKIP", "explicit --skip-build")
    if config.require_engine_binary and not config.engine_exe.exists():
        raise VerificationError(f"missing engine executable: {config.engine_exe}")

    if config.skip_tests is False:
        phase = [
            "tests/test_scrcpy_server.py", "tests/test_engine_process.py",
            "tests/test_engine_auth.py", "tests/test_engine_admin.py",
            "tests/test_engine_runtime.py", "tests/test_engine_orchestrator.py",
            "tests/test_scrcpy_session.py", "tests/test_instance_manager.py",
            "tests/test_app.py", "tests/test_main.py",
        ]
        command = ["uv", "run", "pytest", *phase, "-v"]
        _record_command(deps, command, "phase-specific Python tests")
        deps.run(command, cwd=config.repo_root, env=dict(os.environ), label="phase-specific Python tests")
        engine_tests = config.repo_root / "engine" / "build" / "Release" / "engine_tests.exe"
        if not engine_tests.exists():
            raise VerificationError(f"missing engine_tests.exe: {engine_tests}")
        command = [str(engine_tests), "--gtest_filter=-SignalingClient.*:PublicSignalingBridge.*"]
        _record_command(deps, command, "engine tests")
        deps.run(command, cwd=config.repo_root, env=dict(os.environ), label="engine tests")
    else:
        result.mark("local tests", "SKIP", "explicit --skip-tests")

    vms = list(deps.discover_vms())
    if config.serial:
        vm = next((item for item in vms if _strip_serial(item["id"]) == config.serial), None)
    else:
        ready = set(deps.adb_devices())
        candidates = [item for item in vms if _strip_serial(item["id"]) in ready]
        if len(candidates) != 1:
            raise VerificationError(f"expected one ready discovered emulator, found {len(candidates)}")
        vm = candidates[0]
    if vm is None:
        raise VerificationError(f"selected serial not present in repository discovery: {config.serial}")
    serial = _strip_serial(vm["id"])
    if deps.adb_state(serial) != "device":
        raise VerificationError(f"ADB serial is not ready: {serial}")
    index = int(vm["ldplayer_index"])
    scrcpy_port = 27183 + index
    scid = index
    result.summary.update({"serial": serial, "ldplayer_index": index, "scrcpy_port": scrcpy_port, "scid": scid})
    _trace(deps, f"selected serial={serial} ldplayer_index={index} scrcpy_port={scrcpy_port} scid={scid}")

    saved = {name: os.environ.get(name) for name in _ENV_NAMES}
    owned: list[Any] = []
    owned_engine_pids: set[int] = set()
    app = None
    try:
        os.environ.update({
            "ENGINE_EXE_PATH": str(config.engine_exe),
            "ENGINE_WHEP_CAPABILITY_SECRET": __import__("secrets").token_hex(32),
            "ENGINE_SIGNALING_SECRET": "",
            "ENGINE_LOCAL_ICE_SERVERS": "",
            "ENGINE_PUBLIC_ICE_SERVERS": "",
            "VPS_SIGNALING_URL": f"ws://127.0.0.1:{config.relay_port}",
        })
        for name in ("AUTH_TOKEN", "PUBLIC_UI_URL", "TUNNEL_SECRET"):
            os.environ.pop(name, None)
        env = dict(os.environ)
        relay = deps.start(
            ["uv", "run", "python", "engine/test/local_signaling_server.py", "--host", "127.0.0.1", "--port", str(config.relay_port)],
            cwd=config.repo_root, env=env, stdout_path=config.evidence_dir / "relay.log", label="local signaling relay",
        )
        app = deps.start(
            ["uv", "run", "python", "src/main.py"], cwd=config.repo_root, env=env,
            stdout_path=config.evidence_dir / "app.log", label="WindowControl app",
        )
        owned.extend((relay, app))

        base = f"http://127.0.0.1:{config.app_port}"
        instances: list[dict[str, Any]] = []
        _loop_until(
            deps, lambda: bool((instances := deps.api_instances()) and any(item.get("serial") == serial for item in instances)),
            timeout=90, description="WindowControl discovery", interval=config.poll_seconds,
        )
        engine_processes = list(deps.list_engine_processes())
        if len(engine_processes) != 1:
            raise VerificationError(f"discovery started {len(engine_processes)} engine.exe processes, expected one")
        owned_engine_pids = {int(item.pid) for item in engine_processes}
        result.mark("discovery", "PASS", f"owned engine pid {sorted(owned_engine_pids)[0]}")

        selection = deps.api_select(serial)
        if not selection.get("whep_token"):
            raise VerificationError("engine-select returned no WHEP token")
        if _is_loopback(selection["whep_url"]):
            raise VerificationError("engine-select returned a loopback WHEP URL")
        result.summary["initial_selection"] = _safe_selection(selection)
        result.mark("selection", "PASS", selection["whep_url"])

        page = deps.start(
            ["uv", "run", "python", "-m", "http.server", str(config.page_port), "--directory", "engine/test"],
            cwd=config.repo_root, env=env, stdout_path=config.evidence_dir / "page.log", label="verifier page server",
        )
        owned.append(page)
        page_url = f"http://127.0.0.1:{config.page_port}/python_orchestration_verifier.html{_fragment(selection)}"
        deps.open_browser(page_url)
        result.summary["initial_page"] = page_url.split("#", 1)[0] + "#<redacted>"
        if not _ask(deps, result, "first peer", "Confirm non-black live video, climbing framesDecoded, open DataChannel, and click input; type PASS"):
            return result

        if config.skip_expiry:
            result.mark("expiry", "SKIP", "explicit --skip-expiry")
        else:
            try:
                expiry = int(selection["whep_token"].split(".", 1)[0])
            except (KeyError, ValueError):
                raise VerificationError("WHEP token has no parseable expiry")
            while deps.clock() <= expiry:
                deps.sleep(min(config.expiry_poll_seconds, expiry + config.expiry_grace_seconds - deps.clock()))
            fresh = deps.api_select(serial)
            if fresh["whep_token"] == selection["whep_token"]:
                raise VerificationError("post-expiry selection returned the same token")
            fresh_page = f"http://127.0.0.1:{config.page_port}/python_orchestration_verifier.html{_fragment(fresh)}"
            deps.open_browser(fresh_page)
            result.mark("expiry", "PASS", "fresh token opened in an independent verifier page")
            if not _ask(deps, result, "second peer", "Confirm the fresh verifier independently negotiates live video; type PASS"):
                return result

        before_quality = selection
        target_tier = "1080" if config.tier != "1080" else "720"
        deps.api_quality(serial, target_tier)
        quality = None

        def quality_ready() -> bool:
            nonlocal quality
            quality = deps.api_select(serial)
            _trace(deps, f"quality poll generation={quality.get('generation')} dimensions={quality.get('w')}x{quality.get('h')}")
            return quality["generation"] > before_quality["generation"] and quality.get("w") != before_quality.get("w")

        _loop_until(deps, quality_ready, timeout=45, description="quality reconnect generation/dimensions", interval=config.poll_seconds)
        if quality["whep_url"] != before_quality["whep_url"]:
            raise VerificationError("quality reconnect replaced the WHEP endpoint")
        result.summary["quality"] = _safe_selection(quality)
        if not _ask(deps, result, "quality", "Confirm the existing peer stayed connected and renders the new dimensions; type PASS"):
            return result

        before_source = quality
        deps.kill_scrcpy(serial, scid)
        source = None

        def source_ready() -> bool:
            nonlocal source
            source = deps.api_select(serial)
            _trace(deps, f"scrcpy recovery poll generation={source.get('generation')}")
            return source["generation"] > before_source["generation"]

        _loop_until(deps, source_ready, timeout=45, description="scrcpy watchdog recovery", interval=config.poll_seconds)
        if {int(item.pid) for item in deps.list_engine_processes()} != owned_engine_pids:
            raise VerificationError("scrcpy recovery changed the owned engine process")
        if source["whep_url"] != before_source["whep_url"]:
            raise VerificationError("scrcpy recovery changed the WHEP endpoint")
        if not any(f"tcp:{scrcpy_port}" in item for item in deps.adb_forwards(serial)):
            raise VerificationError("scrcpy recovery lost its ADB forward")
        result.summary["source_recovery"] = _safe_selection(source)
        if not _ask(deps, result, "scrcpy recovery", "Confirm the existing peer resumed live video; type PASS"):
            return result

        old_engine_pid = next(iter(owned_engine_pids))
        deps.kill_owned_engine(old_engine_pid)
        respawn = None

        def respawn_ready() -> bool:
            nonlocal respawn
            candidates = list(deps.list_engine_processes())
            if len(candidates) != 1 or int(candidates[0].pid) == old_engine_pid:
                return False
            respawn = deps.api_select(serial)
            _trace(deps, f"engine respawn poll pids={[int(item.pid) for item in candidates]} whep={respawn.get('whep_url')}")
            return respawn["whep_url"] != source["whep_url"]

        _loop_until(deps, respawn_ready, timeout=60, description="engine respawn and dynamic WHEP port", interval=config.poll_seconds)
        owned_engine_pids = {int(item.pid) for item in deps.list_engine_processes()}
        if not any(f"tcp:{scrcpy_port}" in item for item in deps.adb_forwards(serial)):
            raise VerificationError("engine respawn did not retain exactly this instance's scrcpy forward")
        result.summary["respawn"] = _safe_selection(respawn)
        fresh_respawn_page = f"http://127.0.0.1:{config.page_port}/python_orchestration_verifier.html{_fragment(respawn)}"
        deps.open_browser(fresh_respawn_page)
        if not _ask(deps, result, "engine respawn", "Confirm the freshly opened verifier renders live video; type PASS"):
            return result

        try:
            deps.remove_device(serial)
        except VerificationError as error:
            if deps.prompt(f"Automatic removal failed ({error}); disconnect the selected emulator manually, then type PASS") != "PASS":
                result.mark("emulator removal", "FAIL", "automatic and manual removal failed")
                return result
        _loop_until(deps, lambda: serial not in deps.adb_devices(), timeout=30, description="selected emulator removal", interval=config.poll_seconds)
        _loop_until(deps, lambda: not (owned_engine_pids & {int(item.pid) for item in deps.list_engine_processes()}), timeout=30, description="removed instance engine cleanup", interval=config.poll_seconds)
        _loop_until(deps, lambda: not any(item.get("serial") == serial for item in deps.api_instances()), timeout=30, description="API removal of selected instance", interval=config.poll_seconds)
        if any(f"tcp:{scrcpy_port}" in item for item in deps.adb_forwards(serial)):
            raise VerificationError("selected emulator removal left an ADB forward")
        if not _ask(deps, result, "emulator removal", "Confirm removal left no engine process or instance forward; type PASS"):
            return result

        if deps.prompt("Use the WindowControl tray Exit now, then type PASS") != "PASS":
            result.mark("application exit", "FAIL", "operator did not confirm tray Exit")
            return result
        try:
            _loop_until(deps, lambda: not deps.list_app_processes(), timeout=30, description="operator tray Exit", interval=config.poll_seconds)
        except VerificationError as error:
            result.mark("application exit", "FAIL", str(error))
            return result
        if deps.list_engine_processes():
            result.mark("application exit", "FAIL", "engine process remains after tray Exit")
            return result
        if deps.adb_forwards(serial):
            result.mark("application exit", "FAIL", "ADB forward remains after tray Exit")
            return result
        result.mark("application exit", "PASS", "tray Exit observed and owned engine processes gone")
        result.status = "INCOMPLETE" if any(item["status"] == "SKIP" for item in result.checkpoints.values()) else result.status
        result.summary["status"] = result.status
        return result
    finally:
        # Never force-kill the WindowControl app. On an incomplete/failed run,
        # leave the app, owned engine, and selected forward for diagnostics when
        # requested; helper relay/page processes are safe to stop otherwise.
        failed = result.status in {"FAIL", "INCOMPLETE"}
        if not (failed and config.keep_on_failure):
            for process in owned:
                if process is not app:
                    deps.stop_helper(process)
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        service_log = Path(r"C:\ProgramData\WindowControl\service_crash.log")
        if service_log.exists():
            try:
                shutil.copyfile(service_log, config.evidence_dir / "service_crash.log")
            except OSError:
                _trace(deps, "service log copy unavailable")


class RealDeps:
    """Windows adapters used by the PowerShell entry point."""

    def __init__(self, config: VerificationConfig):
        self.config = config
        self._log = config.evidence_dir / "commands.log"
        self._log.parent.mkdir(parents=True, exist_ok=True)

    def record_command(self, command: list[str], label: str) -> None:
        with self._log.open("a", encoding="utf-8") as stream:
            stream.write(f"{label}: {' '.join(command)}\n")

    def record_event(self, message: str) -> None:
        with (self.config.evidence_dir / "verification.log").open("a", encoding="utf-8") as stream:
            stream.write(f"{time.time():.3f} {message}\n")

    def run(self, command, *, cwd, env, label):
        self.record_command(command, label)
        completed = subprocess.run(command, cwd=cwd, env=env, text=True, capture_output=True)
        with self._log.open("a", encoding="utf-8") as stream:
            stream.write(completed.stdout)
            stream.write(completed.stderr)
        if completed.returncode:
            raise VerificationError(f"{label} failed with exit code {completed.returncode}")

    def start(self, command, *, cwd, env, stdout_path, label):
        self.record_command(command, label)
        output = stdout_path.open("a", encoding="utf-8")
        process = subprocess.Popen(command, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT, text=True)
        process._verification_output = output
        return process

    def stop_helper(self, process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        output = getattr(process, "_verification_output", None)
        if output:
            output.close()

    def list_engine_processes(self):
        import psutil

        processes = [p for p in psutil.process_iter(["pid", "name", "cmdline"]) if (p.info.get("name") or "").lower() == "engine.exe"]
        self.record_event(f"engine processes={[p.pid for p in processes]}")
        return processes

    def discover_vms(self):
        from server.adb_manager import list_vms

        return list_vms()

    def adb(self, args):
        self.record_command(["adb", *args], "adb")
        return subprocess.run(["adb", *args], text=True, capture_output=True, check=False)

    def adb_devices(self):
        result = self.adb(["devices"])
        devices = [line.split()[0] for line in result.stdout.splitlines() if len(line.split()) >= 2 and line.split()[1] == "device"]
        self.record_event(f"adb devices={devices}")
        return devices

    def adb_state(self, serial):
        return self.adb(["-s", serial, "get-state"]).stdout.strip()

    def adb_forwards(self, serial):
        forwards = self.adb(["-s", serial, "forward", "--list"]).stdout.splitlines()
        self.record_event(f"adb forwards count={len(forwards)}")
        return forwards

    def kill_scrcpy(self, serial, scid):
        self.adb(["-s", serial, "shell", f"pkill -f 'scrcpy-server.*scid={scid:x}'"])

    def remove_device(self, serial):
        if serial.startswith("emulator-"):
            completed = self.adb(["-s", serial, "emu", "kill"])
        else:
            completed = self.adb(["disconnect", serial])
        if completed.returncode:
            raise VerificationError("automatic emulator removal failed; disconnect it manually and retry")

    def list_app_processes(self):
        import psutil

        processes = [p for p in psutil.process_iter(["pid", "cmdline"]) if any("src\\main.py" in str(part) or "src/main.py" in str(part) for part in (p.info.get("cmdline") or []))]
        self.record_event(f"app processes={[p.pid for p in processes]}")
        return processes

    def kill_owned_engine(self, pid):
        import psutil

        psutil.Process(pid).terminate()

    def api_instances(self):
        import httpx

        response = httpx.get(f"http://127.0.0.1:{self.config.app_port}/instances", timeout=3)
        response.raise_for_status()
        value = response.json()
        self.record_event(f"GET /instances count={len(value)}")
        return value

    def api_select(self, serial):
        import httpx

        response = httpx.post(f"http://127.0.0.1:{self.config.app_port}/instances/{serial}/engine-select", json={}, timeout=10)
        response.raise_for_status()
        value = response.json()
        self.record_event(f"POST /engine-select serial={serial} generation={value.get('generation')} whep={value.get('whep_url')}")
        return value

    def api_quality(self, serial, tier):
        import httpx

        response = httpx.post(f"http://127.0.0.1:{self.config.app_port}/instances/{serial}/quality", json={"tier": tier}, timeout=60)
        response.raise_for_status()
        value = response.json()
        self.record_event(f"POST /quality tier={tier} status={response.status_code}")
        return value

    def open_browser(self, url):
        webbrowser.open(url)

    def prompt(self, message):
        return input(message + ": ").strip()

    def clock(self):
        return time.time()

    def sleep(self, seconds):
        time.sleep(seconds)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--engine-exe", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--serial", default=None)
    parser.add_argument("--tier", default="720")
    parser.add_argument("--relay-port", type=int, default=8443)
    parser.add_argument("--page-port", type=int, default=8090)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-expiry", action="store_true")
    parser.add_argument("--keep-on-failure", action="store_true")
    args = parser.parse_args()
    config = VerificationConfig(
        repo_root=args.repo_root, engine_exe=args.engine_exe, evidence_dir=args.evidence_dir,
        serial=args.serial, tier=args.tier, relay_port=args.relay_port, page_port=args.page_port,
        skip_build=args.skip_build, skip_tests=args.skip_tests,
        skip_expiry=args.skip_expiry, keep_on_failure=args.keep_on_failure,
    )
    deps = RealDeps(config)
    try:
        result = run_verification(config, deps)
    except VerificationError as error:
        result = VerificationResult(status="FAIL", summary={"error": str(error)})
        config.evidence_dir.mkdir(parents=True, exist_ok=True)
        (config.evidence_dir / "result.json").write_text(json.dumps(result.__dict__, indent=2), encoding="utf-8")
        print(f"FAIL: {error}")
        return 1
    except Exception as error:
        result = VerificationResult(status="FAIL", summary={"error": f"unexpected: {error}"})
        config.evidence_dir.mkdir(parents=True, exist_ok=True)
        (config.evidence_dir / "result.json").write_text(json.dumps(result.__dict__, indent=2), encoding="utf-8")
        print(f"FAIL: unexpected error: {error}")
        return 1
    result.summary["status"] = result.status
    (config.evidence_dir / "result.json").write_text(json.dumps(result.__dict__, indent=2), encoding="utf-8")
    print(json.dumps(result.__dict__, indent=2))
    return 0 if result.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
