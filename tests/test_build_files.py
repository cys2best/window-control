from pathlib import Path

BUILD_DIR = Path(__file__).parent.parent / "build"


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
