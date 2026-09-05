"""Tests for scripts/verify_frontend_cutover.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.verify_frontend_cutover import (
    GATE_NAMES,
    FrontendCutoverConfig,
    FrontendCutoverResult,
    RealFrontendDeps,
    _parse_jest_summary,
    _parse_pytest_summary,
    gate_auth_gate,
    gate_dev_app_health,
    gate_instances_negotiation,
    gate_offline_suites,
    gate_rsc_payloads,
    gate_web_routes,
    resolve_gate_selection,
)


def _config(**overrides) -> FrontendCutoverConfig:
    defaults = dict(
        repo_root=Path("/repo"),
        evidence_dir=Path("/repo/engine/test/frontend-cutover-x"),
        web_build_dir=Path("/repo/apps/web/out"),
        installer_path=Path("/repo/release/WindowControlInstaller.exe"),
    )
    defaults.update(overrides)
    return FrontendCutoverConfig(**defaults)


def test_full_run_selects_every_gate_as_requested():
    selection = resolve_gate_selection(_config())
    assert set(selection) == set(GATE_NAMES)
    assert all(reason == "requested" for reason in selection.values())


def test_only_a_dependency_free_gate_selects_just_that_gate():
    selection = resolve_gate_selection(_config(only=("offline_suites",)))
    assert selection["offline_suites"] == "requested"
    for name in GATE_NAMES:
        if name != "offline_suites":
            assert selection[name] == "not selected this run (--only)"


def test_only_a_dependent_gate_auto_includes_its_prerequisite():
    selection = resolve_gate_selection(_config(only=("auth_gate",)))
    assert selection["auth_gate"] == "requested"
    assert selection["dev_app_health"] == "auto-included as prerequisite of auth_gate"
    assert selection["web_routes"] == "not selected this run (--only)"


def test_only_frozen_selfrelaunch_auto_includes_installed_app_launch():
    selection = resolve_gate_selection(_config(only=("frozen_selfrelaunch",)))
    assert selection["frozen_selfrelaunch"] == "requested"
    assert selection["installed_app_launch"] == "auto-included as prerequisite of frozen_selfrelaunch"


def test_only_accepts_a_comma_style_tuple_of_multiple_gates():
    selection = resolve_gate_selection(_config(only=("web_routes", "rsc_payloads")))
    assert selection["web_routes"] == "requested"
    assert selection["rsc_payloads"] == "requested"
    assert selection["dev_app_health"] == "auto-included as prerequisite of web_routes"
    assert selection["instances_negotiation"] == "not selected this run (--only)"


def test_from_a_gate_selects_it_and_everything_after_in_fixed_order():
    selection = resolve_gate_selection(_config(from_gate="offline_suites"))
    index = GATE_NAMES.index("offline_suites")
    for i, name in enumerate(GATE_NAMES):
        expected = "requested" if i >= index else "not selected this run (--from)"
        assert selection[name] == expected


def test_from_the_first_gate_selects_everything():
    selection = resolve_gate_selection(_config(from_gate="dev_app_health"))
    assert all(reason == "requested" for reason in selection.values())


def test_only_and_from_together_is_rejected():
    with pytest.raises(ValueError, match="mutually exclusive"):
        resolve_gate_selection(_config(only=("web_routes",), from_gate="offline_suites"))


def test_unknown_gate_name_in_only_is_rejected():
    with pytest.raises(ValueError, match="unknown gate"):
        resolve_gate_selection(_config(only=("not_a_real_gate",)))


def test_unknown_gate_name_in_from_is_rejected():
    with pytest.raises(ValueError, match="unknown gate"):
        resolve_gate_selection(_config(from_gate="not_a_real_gate"))


def test_result_mark_pass_keeps_status_pass():
    result = FrontendCutoverResult()
    result.mark("dev_app_health", "PASS", reason="requested")
    assert result.status == "PASS"
    assert result.gates["dev_app_health"] == {"status": "PASS", "reason": "requested", "details": {}}


def test_result_mark_fail_sets_overall_status_fail():
    result = FrontendCutoverResult()
    result.mark("web_routes", "FAIL", reason="requested", details={"error": "500"})
    assert result.status == "FAIL"


def test_result_mark_skipped_caps_status_at_incomplete_not_pass():
    result = FrontendCutoverResult()
    result.mark("leaked_key_forgery_check", "SKIPPED", reason="no second machine available")
    assert result.status == "INCOMPLETE"


def test_result_mark_skipped_does_not_downgrade_an_existing_fail():
    result = FrontendCutoverResult()
    result.mark("web_routes", "FAIL", reason="requested")
    result.mark("auth_gate", "SKIPPED", reason="Supabase auth not configured this run")
    assert result.status == "FAIL"


def test_to_dict_omits_selection_key_on_a_full_run():
    result = FrontendCutoverResult()
    result.mark("dev_app_health", "PASS", reason="requested")
    payload = result.to_dict()
    assert "selection" not in payload
    assert payload == {"status": "PASS", "gates": {"dev_app_health": {"status": "PASS", "reason": "requested", "details": {}}}}


class FakeDeps:
    def __init__(self, *, auth_config: dict | None = None, responses: dict[tuple[int, str], tuple[int, str, str]] | None = None):
        self._auth_config = auth_config if auth_config is not None else {"auth_enabled": False, "supabase_url": "", "supabase_anon_key": ""}
        self._responses = responses or {}
        self.started_dev_app = False
        self.terminated: list[str] = []
        self.dev_app_wait_succeeds = True

    def start_dev_app(self, environment):
        self.started_dev_app = True
        return __import__("scripts.verify_lib", fromlist=["OwnedProcess"]).OwnedProcess("app", 4242, 1.0)

    def wait_for_dev_app(self, app, port):
        return self.dev_app_wait_succeeds

    def auth_config(self, port):
        return self._auth_config

    def get(self, port, path, *, headers=None):
        accept = headers.get("Accept", "") if headers else ""
        for key in ((port, f"{path}|{accept}"), (port, path)):
            if key in self._responses:
                return self._responses[key]
        return (404, "text/plain", "not found")

    def terminate(self, process):
        self.terminated.append(process.kind)


def test_dev_app_health_passes_when_wait_succeeds():
    result = FrontendCutoverResult()
    deps = FakeDeps()
    gate_dev_app_health(_config(), deps, result, "requested")
    assert deps.started_dev_app is True
    assert result.gates["dev_app_health"]["status"] == "PASS"


def test_dev_app_health_fails_when_wait_times_out():
    result = FrontendCutoverResult()
    deps = FakeDeps()
    deps.dev_app_wait_succeeds = False
    gate_dev_app_health(_config(), deps, result, "requested")
    assert result.gates["dev_app_health"]["status"] == "FAIL"


def test_web_routes_passes_when_every_page_is_html_200():
    responses = {(8080, path): (200, "text/html; charset=utf-8", "<html></html>") for path in ("/", "/login", "/setup", "/stream")}
    result = FrontendCutoverResult()
    deps = FakeDeps(responses=responses)
    gate_web_routes(_config(), deps, result, "requested")
    assert result.gates["web_routes"]["status"] == "PASS"


def test_web_routes_fails_when_one_page_is_not_html():
    responses = {(8080, path): (200, "text/html; charset=utf-8", "<html></html>") for path in ("/", "/login", "/setup", "/stream")}
    responses[(8080, "/stream")] = (200, "application/json", "{}")
    result = FrontendCutoverResult()
    deps = FakeDeps(responses=responses)
    gate_web_routes(_config(), deps, result, "requested")
    assert result.gates["web_routes"]["status"] == "FAIL"
    assert "/stream" in result.gates["web_routes"]["details"]["failures"][0]


def test_rsc_payloads_passes_for_the_five_txt_files_plus_static_assets():
    responses = {}
    for page in ("index", "login", "setup", "instances", "stream"):
        responses[(8080, f"/{page}.txt")] = (200, "text/x-component; charset=utf-8", "chunk")
    responses[(8080, "/404.html")] = (200, "text/html; charset=utf-8", "<html></html>")
    responses[(8080, "/manifest.json")] = (200, "application/json", "{}")
    responses[(8080, "/icon-192.png")] = (200, "image/png", "\x89PNG")
    result = FrontendCutoverResult()
    deps = FakeDeps(responses=responses)
    gate_rsc_payloads(_config(), deps, result, "requested")
    assert result.gates["rsc_payloads"]["status"] == "PASS"


def test_rsc_payloads_fails_when_a_txt_payload_404s():
    responses = {}
    for page in ("index", "login", "setup", "instances", "stream"):
        responses[(8080, f"/{page}.txt")] = (200, "text/x-component; charset=utf-8", "chunk")
    responses[(8080, "/stream.txt")] = (404, "text/plain", "not found")
    responses[(8080, "/404.html")] = (200, "text/html; charset=utf-8", "<html></html>")
    responses[(8080, "/manifest.json")] = (200, "application/json", "{}")
    responses[(8080, "/icon-192.png")] = (200, "image/png", "\x89PNG")
    result = FrontendCutoverResult()
    deps = FakeDeps(responses=responses)
    gate_rsc_payloads(_config(), deps, result, "requested")
    assert result.gates["rsc_payloads"]["status"] == "FAIL"


def test_instances_negotiation_passes_when_json_and_html_shapes_are_correct():
    responses = {
        (8080, "/instances|"): (200, "application/json", "[]"),
        (8080, "/instances|application/json"): (200, "application/json", "[]"),
        (8080, "/instances|text/html"): (200, "text/html; charset=utf-8", "<html></html>"),
    }
    result = FrontendCutoverResult()
    deps = FakeDeps(responses=responses)
    gate_instances_negotiation(_config(), deps, result, "requested")
    assert result.gates["instances_negotiation"]["status"] == "PASS"


def test_instances_negotiation_fails_when_html_accept_still_returns_json():
    responses = {
        (8080, "/instances|"): (200, "application/json", "[]"),
        (8080, "/instances|application/json"): (200, "application/json", "[]"),
        (8080, "/instances|text/html"): (200, "application/json", "[]"),
    }
    result = FrontendCutoverResult()
    deps = FakeDeps(responses=responses)
    gate_instances_negotiation(_config(), deps, result, "requested")
    assert result.gates["instances_negotiation"]["status"] == "FAIL"


def test_instances_negotiation_fails_when_html_status_is_not_200():
    responses = {
        (8080, "/instances|"): (200, "application/json", "[]"),
        (8080, "/instances|application/json"): (200, "application/json", "[]"),
        (8080, "/instances|text/html"): (500, "text/html; charset=utf-8", "<html>server error</html>"),
    }
    result = FrontendCutoverResult()
    deps = FakeDeps(responses=responses)
    gate_instances_negotiation(_config(), deps, result, "requested")
    assert result.gates["instances_negotiation"]["status"] == "FAIL"
    assert any("HTML" in f for f in result.gates["instances_negotiation"]["details"]["failures"])


def test_auth_gate_self_skips_when_auth_is_disabled():
    result = FrontendCutoverResult()
    deps = FakeDeps(auth_config={"auth_enabled": False, "supabase_url": "", "supabase_anon_key": ""})
    gate_auth_gate(_config(), deps, result, "requested")
    assert result.gates["auth_gate"]["status"] == "SKIPPED"
    assert "not configured" in result.gates["auth_gate"]["details"]["reason"]


def test_auth_gate_passes_when_both_unauthenticated_requests_401():
    responses = {
        (8080, "/instances|"): (401, "application/json", '{"detail": "Not authenticated"}'),
    }
    result = FrontendCutoverResult()
    deps = FakeDeps(auth_config={"auth_enabled": True, "supabase_url": "x", "supabase_anon_key": "y"}, responses=responses)

    def get(port, path, *, headers=None):
        return (401, "application/json", '{"detail": "Not authenticated"}')

    deps.get = get
    gate_auth_gate(_config(), deps, result, "requested")
    assert result.gates["auth_gate"]["status"] == "PASS"


def test_auth_gate_fails_when_a_bad_token_is_accepted():
    result = FrontendCutoverResult()
    deps = FakeDeps(auth_config={"auth_enabled": True, "supabase_url": "x", "supabase_anon_key": "y"})

    def get(port, path, *, headers=None):
        return (200, "application/json", "[]")  # should have been 401

    deps.get = get
    gate_auth_gate(_config(), deps, result, "requested")
    assert result.gates["auth_gate"]["status"] == "FAIL"


def test_dev_app_health_scrubs_auth_token(monkeypatch):
    monkeypatch.setenv("AUTH_TOKEN", "secret-test-token")
    result = FrontendCutoverResult()
    recorded_env = {}

    class EnvFakeDeps(FakeDeps):
        def start_dev_app(self, environment):
            recorded_env.update(environment)
            return super().start_dev_app(environment)

    deps = EnvFakeDeps()
    gate_dev_app_health(_config(), deps, result, "requested")
    assert "AUTH_TOKEN" not in recorded_env
    assert result.gates["dev_app_health"]["status"] == "PASS"


def test_real_deps_get_and_auth_config(monkeypatch):
    config = _config()
    deps = RealFrontendDeps(config)

    class MockResponse:
        def __init__(self, status_code, headers, text, json_data):
            self.status_code = status_code
            self.headers = headers
            self.text = text
            self._json_data = json_data

        def raise_for_status(self):
            pass

        def json(self):
            return self._json_data

    def mock_get(url, **kwargs):
        if url.endswith("/auth/config"):
            return MockResponse(200, {"content-type": "application/json"}, '{"auth_enabled": true}', {"auth_enabled": True})
        return MockResponse(200, {"content-type": "text/html"}, "<html></html>", None)

    monkeypatch.setattr("httpx.get", mock_get)

    assert deps.auth_config(8080) == {"auth_enabled": True}
    status, content_type, text = deps.get(8080, "/")
    assert status == 200
    assert content_type == "text/html"
    assert text == "<html></html>"


def test_real_deps_wait_for_dev_app(monkeypatch):
    from scripts.verify_lib import OwnedProcess

    config = _config()
    deps = RealFrontendDeps(config)
    app = OwnedProcess("dev_app", 1234, 1.0)

    monkeypatch.setattr("scripts.verify_frontend_cutover._pid_started_at", lambda pid: 1.0)

    class MockResponse:
        status_code = 200

    monkeypatch.setattr("httpx.get", lambda url, **kwargs: MockResponse())
    assert deps.wait_for_dev_app(app, 8080) is True


def test_real_deps_wait_for_dev_app_process_died(monkeypatch):
    from scripts.verify_lib import OwnedProcess

    config = _config()
    deps = RealFrontendDeps(config)
    app = OwnedProcess("dev_app", 1234, 1.0)

    monkeypatch.setattr("scripts.verify_frontend_cutover._pid_started_at", lambda pid: None)
    assert deps.wait_for_dev_app(app, 8080) is False


def test_real_deps_terminate_skips_if_pid_mismatch(monkeypatch):
    from scripts.verify_lib import OwnedProcess

    config = _config()
    deps = RealFrontendDeps(config)
    app = OwnedProcess("dev_app", 1234, 1.0)
    monkeypatch.setattr("scripts.verify_frontend_cutover._pid_started_at", lambda pid: 2.0)
    deps.terminate(app)


def test_real_deps_terminate_kills_process(monkeypatch):
    from scripts.verify_lib import OwnedProcess

    config = _config()
    deps = RealFrontendDeps(config)
    app = OwnedProcess("dev_app", 1234, 1.0)
    monkeypatch.setattr("scripts.verify_frontend_cutover._pid_started_at", lambda pid: 1.0)

    terminated = []

    class FakeProc:
        def terminate(self):
            terminated.append(True)

    monkeypatch.setattr("psutil.Process", lambda pid: FakeProc())
    deps.terminate(app)
    assert terminated == [True]


def test_real_deps_start_dev_app(monkeypatch, tmp_path):
    evidence_dir = tmp_path / "evidence"
    config = _config(evidence_dir=evidence_dir)
    deps = RealFrontendDeps(config)

    class FakePopen:
        pid = 9999

    def mock_popen(*args, **kwargs):
        return FakePopen()

    monkeypatch.setattr("subprocess.Popen", mock_popen)

    class FakeProc:
        def create_time(self):
            return 123.456

    monkeypatch.setattr("psutil.Process", lambda pid: FakeProc())

    proc = deps.start_dev_app({})
    assert proc.kind == "dev_app"
    assert proc.pid == 9999
    assert proc.started_at == 123.456
    assert (evidence_dir / "dev-app.log").exists()


def test_parse_pytest_summary_extracts_all_four_categories():
    output = "2 failed, 456 passed, 1 skipped, 249 warnings, 2 errors in 25.86s"
    assert _parse_pytest_summary(output) == {"passed": 456, "failed": 2, "skipped": 1, "errors": 2}


def test_parse_pytest_summary_defaults_absent_categories_to_zero():
    output = "45 passed in 3.02s"
    assert _parse_pytest_summary(output) == {"passed": 45, "failed": 0, "skipped": 0, "errors": 0}


def test_parse_jest_summary_extracts_from_the_tests_line_only():
    output = "Test Suites: 1 failed, 9 passed, 10 total\nTests:       2 failed, 43 passed, 45 total\n"
    assert _parse_jest_summary(output) == {"passed": 43, "failed": 2, "total": 45}


def test_parse_jest_summary_handles_zero_failures():
    output = "Test Suites: 10 passed, 10 total\nTests:       45 passed, 45 total\n"
    assert _parse_jest_summary(output) == {"passed": 45, "failed": 0, "total": 45}


def test_gate_offline_suites_passes_when_every_command_exits_zero_with_zero_failures():
    class FakeDepsForSuites:
        def run_command(self, command, *, cwd=None, timeout=600):
            if "pytest" in command:
                return 0, "456 passed in 20s"
            return 0, "Tests:       10 passed, 10 total\n"

    result = FrontendCutoverResult()
    gate_offline_suites(_config(), FakeDepsForSuites(), result, "requested")
    assert result.gates["offline_suites"]["status"] == "PASS"


def test_gate_offline_suites_fails_when_a_suite_reports_failures_even_with_exit_zero():
    class FakeDepsForSuites:
        def run_command(self, command, *, cwd=None, timeout=600):
            if "pytest" in command and "apps/desktop" not in " ".join(command):
                return 0, "2 failed, 456 passed in 20s"
            return 0, "Tests:       10 passed, 10 total\n"

    result = FrontendCutoverResult()
    gate_offline_suites(_config(), FakeDepsForSuites(), result, "requested")
    assert result.gates["offline_suites"]["status"] == "FAIL"


def test_gate_offline_suites_fails_when_a_command_exits_nonzero():
    class FakeDepsForSuites:
        def run_command(self, command, *, cwd=None, timeout=600):
            return 1, "collection error"

    result = FrontendCutoverResult()
    gate_offline_suites(_config(), FakeDepsForSuites(), result, "requested")
    assert result.gates["offline_suites"]["status"] == "FAIL"


def test_real_deps_run_command_success(monkeypatch):
    import subprocess
    config = _config()
    deps = RealFrontendDeps(config)

    class FakeCompleted:
        returncode = 0
        stdout = "output line\n"
        stderr = "err line\n"

    def mock_run(command, cwd=None, text=True, capture_output=True, timeout=600, **kwargs):
        assert command == ["echo", "hello"]
        assert cwd == config.repo_root
        return FakeCompleted()

    monkeypatch.setattr("subprocess.run", mock_run)
    code, output = deps.run_command(["echo", "hello"])
    assert code == 0
    assert output == "output line\nerr line\n"


def test_real_deps_run_command_timeout(monkeypatch):
    import subprocess
    config = _config()
    deps = RealFrontendDeps(config)

    def mock_run(command, cwd=None, text=True, capture_output=True, timeout=600, **kwargs):
        raise subprocess.TimeoutExpired(cmd=command, timeout=timeout)

    monkeypatch.setattr("subprocess.run", mock_run)
    code, output = deps.run_command(["sleep", "10"], timeout=5)
    assert code == 1
    assert "timed out after 5s" in output



