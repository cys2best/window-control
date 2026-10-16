# tests/test_assets.py
import os
import importlib.util
from pathlib import Path

ASSETS_DIR = Path(__file__).parent.parent / "src" / "assets"


def test_tray_icon_exists():
    path = ASSETS_DIR / "tray_icon.png"
    assert path.exists(), f"Missing {path}"
    assert path.stat().st_size > 0


def test_icon_ico_exists():
    path = ASSETS_DIR / "icon.ico"
    assert path.exists(), f"Missing {path}"
    assert path.stat().st_size > 0


def test_tray_icon_is_valid_png():
    from PIL import Image
    img = Image.open(ASSETS_DIR / "tray_icon.png")
    assert img.size == (64, 64)


def test_ico_is_valid():
    from PIL import Image
    img = Image.open(ASSETS_DIR / "icon.ico")
    assert img.size[0] > 0


def test_download_manifest_contains_scrcpy_only():
    script = Path(__file__).parent.parent / "scripts" / "download_assets.py"
    spec = importlib.util.spec_from_file_location("download_assets_for_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert [item["name"] for item in module.DOWNLOADS] == ["scrcpy"]
    assert module.DOWNLOADS[0]["check"].endswith(
        os.path.join("assets", "scrcpy", "scrcpy.exe")
    )
