from pathlib import Path

BUILD_DIR = Path(__file__).parent.parent / "build"
REPO_ROOT = Path(__file__).parent.parent


def test_spec_file_exists():
    assert (BUILD_DIR / "window_control.spec").exists()


def test_build_bat_exists():
    assert (BUILD_DIR / "build.bat").exists()


def test_installer_bat_exists():
    assert (BUILD_DIR / "build_installer.bat").exists()


def test_installer_iss_exists():
    assert (BUILD_DIR / "installer.iss").exists()


def test_spec_references_main():
    content = (BUILD_DIR / "window_control.spec").read_text()
    assert "main.py" in content
    assert "client" in content
    assert "assets" in content


def test_installer_iss_has_tailscale_check():
    content = (BUILD_DIR / "installer.iss").read_text()
    assert "Tailscale" in content
    assert "OutputBaseFilename=WindowControlInstaller" in content


def test_pyinstaller_contains_engine_without_legacy_media_imports():
    text = (BUILD_DIR / "window_control.spec").read_text()
    assert "assets" in text
    assert "engine" in text
    for legacy in ("aiortc", "imageio_ffmpeg", "av.codec", "aiohttp"):
        assert legacy not in text


def test_installer_owns_engine_program_firewall_rule():
    text = (BUILD_DIR / "installer.iss").read_text()
    assert "WindowControl-Engine" in text
    assert "assets\\engine\\engine.exe" in text
    assert "firewall delete rule" in text.lower()


def test_ci_runs_unfiltered_engine_suite_with_node_relay():
    text = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
    assert "infra/vps/signaling" in text
    assert "npm ci" in text
    assert "engine_tests.exe" in text
    assert "--gtest_filter" not in text
