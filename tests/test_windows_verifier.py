from pathlib import Path


ROOT = Path(__file__).parents[1]
RUNNER = ROOT / "engine" / "verify-python-orchestration.ps1"
PAGE = ROOT / "engine" / "test" / "python_orchestration_verifier.html"
RUNBOOK = ROOT / "engine" / "test" / "README_python_orchestration.md"


def test_one_command_runner_has_safe_defaults_and_cleanup_contract():
    text = RUNNER.read_text()

    assert "param(" in text
    assert "[string]$Serial" in text
    assert "[switch]$SkipBuild" in text
    assert "[switch]$KeepLogs" in text
    assert "Register-EngineCleanup" in text
    assert "try {" in text and "finally {" in text
    assert "Stop-Process" in text
    assert "this run's instance command line" in text
    assert "/quality" in text
    assert "scrcpy-server.*scid=" in text
    assert "$app.Id" in text
    assert "forward --remove" in text
    assert "ENGINE_EXE_PATH" in text
    assert "python_orchestration_verifier.html" in text


def test_runner_covers_the_eight_windows_matrix_checkpoints():
    text = RUNNER.read_text()

    expected_markers = (
        "Discovery starts exactly one engine.exe",
        "non-loopback WHEP URL",
        "fresh selection token",
        "quality/reconnect",
        "scrcpy-server death",
        "engine.exe death",
        "emulator removal",
        "application exit",
    )
    for marker in expected_markers:
        assert marker in text


def test_verifier_page_selects_with_engine_token_and_reports_webrtc_evidence():
    text = PAGE.read_text()

    assert "/engine-select" in text
    assert "Authorization" in text
    assert "Bearer" in text
    assert "framesDecoded" in text
    assert "RTCPeerConnection" in text
    assert "createDataChannel" in text
    assert "input channel open" in text
    assert "generation" in text
    assert "quality" in text


def test_runbook_is_one_command_and_refuses_to_claim_windows_from_darwin():
    text = RUNBOOK.read_text()

    assert ".\\engine\\verify-python-orchestration.ps1" in text
    assert "Windows Host PC" in text
    assert "not verified" in text.lower()
    assert "ENGINE_EXE_PATH" in text
    assert "KeepLogs" in text
