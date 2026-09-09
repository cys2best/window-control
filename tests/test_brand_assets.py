"""Tests for the generated EmuCtrl brand asset set.

These assert the *output* of `npm run generate:brand` (dimensions, colors,
valid ICO structure) -- they don't re-run the generator itself (that's a
Node script, not Python), so run `npm run generate:brand` first if any of
these fail after touching assets/brand/emuctrl-mark.svg.
"""
import os

import pytest
from PIL import Image

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")


def _path(*parts):
    return os.path.join(REPO_ROOT, *parts)


@pytest.mark.parametrize(("path", "size"), [
    ("apps/web/public/icon-192.png", (192, 192)),
    ("apps/web/public/icon-512.png", (512, 512)),
    ("apps/mobile/assets/icon.png", (1024, 1024)),
    ("apps/mobile/assets/android-icon-foreground.png", (432, 432)),
    ("apps/mobile/assets/android-icon-background.png", (432, 432)),
    ("apps/mobile/assets/android-icon-monochrome.png", (432, 432)),
    ("apps/mobile/assets/favicon.png", (48, 48)),
    ("apps/mobile/assets/splash-icon.png", (512, 512)),
    ("src/assets/tray_icon.png", (64, 64)),
])
def test_generated_brand_asset_dimensions(path, size):
    with Image.open(_path(path)) as image:
        assert image.size == size


@pytest.mark.parametrize("path", [
    "apps/web/public/icon-192.png",
    "apps/web/public/icon-512.png",
    "apps/mobile/assets/icon.png",
    "apps/mobile/assets/favicon.png",
])
def test_backed_icons_are_opaque(path):
    """Backed icons (solid #06070b square) must not carry a stray alpha
    channel -- app-store/PWA icon validators reject or flag one."""
    with Image.open(_path(path)) as image:
        assert image.mode in ("RGB", "L")


@pytest.mark.parametrize("path", [
    "apps/mobile/assets/android-icon-foreground.png",
    "apps/mobile/assets/android-icon-monochrome.png",
    "apps/mobile/assets/splash-icon.png",
    "src/assets/tray_icon.png",
])
def test_transparent_layers_carry_alpha(path):
    with Image.open(_path(path)) as image:
        assert image.mode in ("RGBA", "LA")


def test_canonical_svg_uses_the_locked_palette():
    svg = open(_path("assets/brand/emuctrl-mark.svg")).read()
    assert "#00E5FF" in svg
    assert "#E6EAF2" in svg
    assert "#5C6679" in svg


@pytest.mark.parametrize("path", [
    "apps/web/public/favicon.ico",
    "src/assets/icon.ico",
])
def test_ico_files_open_with_pillow(path):
    with Image.open(_path(path)) as image:
        assert image.format == "ICO"
        # png-to-ico was given 16/32/48/256px buffers -- all four frames
        # must actually be embedded, not just the largest one Pillow
        # opens by default.
        sizes = {(0, 0)}
        if hasattr(image, "ico"):
            sizes = image.ico.sizes()
        assert sizes >= {(16, 16), (32, 32), (48, 48), (256, 256)}


def test_load_tray_icon_raises_when_asset_missing(tmp_path, monkeypatch):
    import sys
    sys.path.insert(0, _path("apps/desktop"))
    import importlib
    import tray as tray_mod
    importlib.reload(tray_mod)
    monkeypatch.setattr(tray_mod, "ASSETS_DIR", str(tmp_path))
    with pytest.raises(FileNotFoundError):
        tray_mod._load_tray_icon()
