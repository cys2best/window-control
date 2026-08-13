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


def test_ffmpeg_args_are_copy_not_reencode():
    from server.scrcpy_session import build_ffmpeg_args
    args = build_ffmpeg_args("ffmpeg", "rtsp://localhost:8554/instance0")
    assert "-c:v" in args
    assert args[args.index("-c:v") + 1] == "copy"
    assert "libx264" not in args
    assert "-f" in args and "rtsp" in args
    assert "-rtsp_transport" in args and "tcp" in args
    assert args[-1] == "rtsp://localhost:8554/instance0"


def test_ffmpeg_args_use_wallclock_timestamps():
    # Raw H.264 from scrcpy has no container timestamps; without wallclock
    # stamping ffmpeg guesses 25fps, the RTSP muxer stalls on non-monotonic
    # DTS, and mediamtx times out the publish (~10s 'i/o timeout'). Regression
    # guard: this flag was dropped when copy-mux landed and broke all publishers.
    from server.scrcpy_session import build_ffmpeg_args
    args = build_ffmpeg_args("ffmpeg", "rtsp://localhost:8554/instance0")
    i = args.index("-use_wallclock_as_timestamps")
    assert args[i + 1] == "1"
    # Must come before -i so it applies to the input demuxer.
    assert i < args.index("-i")


def test_set_tier_updates_tier_when_not_running():
    from server.scrcpy_session import ScrcpySession
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    assert s.set_tier("1080") is True
    assert s.tier == "1080"


def test_restart_if_dead_skips_when_alive():
    # Watchdog must not restart a session that is alive — that would double-start
    # a session mid tier-change and corrupt the scrcpy handshake.
    from server.scrcpy_session import ScrcpySession
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    calls = {"start": 0, "stop": 0}
    s.start = lambda: calls.__setitem__("start", calls["start"] + 1) or True
    s.stop = lambda: calls.__setitem__("stop", calls["stop"] + 1)
    # Force alive True via the real attributes the property reads.
    s._running = True

    class _P:  # stand-in ffmpeg proc
        pass
    s._ffmpeg_proc = _P()

    assert s.restart_if_dead() is True
    assert calls == {"start": 0, "stop": 0}  # no restart fired


def test_set_tier_rejects_unknown():
    from server.scrcpy_session import ScrcpySession
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    assert s.set_tier("9000") is False
    assert s.tier == "720"


def test_set_tier_same_is_noop_true():
    from server.scrcpy_session import ScrcpySession
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    assert s.set_tier("720") is True


def test_set_tier_returns_false_when_restart_fails():
    """If the session was running and start() fails on restart, set_tier must
    return False (not silently report success while leaving the session dead)."""
    class _FakeProc:
        def kill(self):
            pass

    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    # Simulate "was running": _running True and a live ffmpeg proc.
    s._running = True
    s._ffmpeg_proc = _FakeProc()
    # Make the restart's start() fail transiently.
    s.start = lambda: False
    assert s.set_tier("1080") is False
