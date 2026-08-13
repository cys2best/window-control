import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from server.scrcpy_session import ScrcpySession


def test_scrcpy_session_not_alive_before_start():
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    assert not s.alive


def test_scrcpy_session_stop_idempotent():
    """stop() on an unstarted session should not raise."""
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    s.stop()
    s.stop()


def test_build_scrcpy_args_uses_tier():
    from server.scrcpy_session import build_scrcpy_args
    from config import QUALITY_TIERS
    args = build_scrcpy_args("1080", scid=0x1a)
    joined = " ".join(args)
    assert f"max_size={QUALITY_TIERS['1080']['max_size']}" in joined
    assert f"bit_rate={QUALITY_TIERS['1080']['bit_rate']}" in joined
    assert f"max_fps={QUALITY_TIERS['1080']['max_fps']}" in joined
    assert "i-frame-interval=1" in joined
    assert "scid=1a" in joined


def test_session_defaults_to_default_tier():
    from server.scrcpy_session import ScrcpySession
    from config import DEFAULT_TIER
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    assert s.tier == DEFAULT_TIER
