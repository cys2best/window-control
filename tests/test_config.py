import os
import sys
from pathlib import Path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from config import PORT, DEV_MODE, get_base_path, WEB_BUILD_DIR, ASSETS_DIR

def test_port_default():
    assert PORT == 8080

def test_dev_mode_on_mac():
    # On Mac (CI), DEV_MODE must be True
    if sys.platform != 'win32':
        assert DEV_MODE is True

def test_base_path_returns_string():
    assert isinstance(get_base_path(), str)

def test_web_build_dir_is_string():
    assert isinstance(WEB_BUILD_DIR, str)


def test_web_build_dir_resolves_development_and_frozen_layouts(monkeypatch):
    import config

    monkeypatch.delattr(config.sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(config, "BASE_PATH", os.path.join("C:\\repo", "src"))
    assert config.get_web_build_dir() == os.path.join(
        "C:\\repo", "apps", "web", "out"
    )

    monkeypatch.setattr(config.sys, "_MEIPASS", "C:\\bundle", raising=False)
    monkeypatch.setattr(config, "BASE_PATH", "C:\\bundle")
    assert config.get_web_build_dir() == os.path.join("C:\\bundle", "web")

def test_quality_tiers_shape():
    from config import QUALITY_TIERS, TIER_ORDER, DEFAULT_TIER
    assert TIER_ORDER == ["480", "720", "1080", "1440"]
    assert DEFAULT_TIER == "720"
    for t in TIER_ORDER:
        tier = QUALITY_TIERS[t]
        assert isinstance(tier["max_size"], int)
        assert tier["bit_rate"].endswith("M")
        assert tier["max_fps"] in (30, 60)

def test_quality_tiers_monotonic():
    from config import QUALITY_TIERS, TIER_ORDER
    sizes = [QUALITY_TIERS[t]["max_size"] for t in TIER_ORDER]
    assert sizes == sorted(sizes)


def test_engine_exe_path_resolves_development_and_frozen_layouts(monkeypatch):
    import config

    monkeypatch.delattr(config.sys, "_MEIPASS", raising=False)
    monkeypatch.setattr(config, "BASE_PATH", os.path.join("C:\\repo", "src"))
    assert config.engine_exe_path() == os.path.join(
        "C:\\repo", "engine", "build", "Release", "engine.exe"
    )

    monkeypatch.setattr(config.sys, "_MEIPASS", "C:\\bundle", raising=False)
    monkeypatch.setattr(config, "ASSETS_DIR", os.path.join("C:\\bundle", "assets"))
    assert config.engine_exe_path() == os.path.join(
        "C:\\bundle", "assets", "engine", "engine.exe"
    )


def test_legacy_runtime_modules_constants_and_dependencies_are_absent():
    repo = Path(__file__).parent.parent
    deleted_modules = [
        "mediamtx_manager.py",
        "webrtc_manager.py",
        "whep_app.py",
        "rtc_engine.py",
        "signaling_bridge.py",
        "publish_hook.py",
        "scrcpy_session.py",
        "stream.py",
    ]
    for filename in deleted_modules:
        assert not (repo / "src" / "server" / filename).exists()

    removed_runtime_names = (
        "WEBRTC_BACKEND",
        "ENGINE_EXE_PATH",
        "mediamtx",
        "aiortc",
        "imageio_ffmpeg",
        "/input",
        "CaptureState",
        "FrameQueue",
        "capture_loop",
        "AdbSession",
        "screenrecord --output-format=h264",
        "mjpeg",
    )
    runtime_text = "\n".join(
        path.read_text(errors="replace")
        for path in (repo / "src").rglob("*.py")
    )
    dependency_text = (repo / "pyproject.toml").read_text()
    asset_script_text = (repo / "scripts" / "download_assets.py").read_text()
    scanned = "\n".join((runtime_text, dependency_text, asset_script_text))
    for removed_name in removed_runtime_names:
        assert removed_name not in scanned


def test_removed_config_exports_are_absent():
    import config

    for name in (
        "ENGINE_EXE_PATH",
        "ENGINE_WHEP_CAPABILITY_SECRET",
        "WEBRTC_BACKEND",
        "MEDIAMTX_PORT",
        "WHEP_PORT",
        "RTMP_PORT",
        "WEBRTC_UDP_PORT",
        "AIORTC_PROFILE_LEVEL_ID",
        "MEDIAMTX_PATH",
        "QUALITY_MAP",
        "DEFAULT_QUALITY",
    ):
        assert not hasattr(config, name)
