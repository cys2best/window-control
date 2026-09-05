"""Tests for scripts/verify_frontend_cutover.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.verify_frontend_cutover import (
    GATE_NAMES,
    FrontendCutoverConfig,
    FrontendCutoverResult,
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
