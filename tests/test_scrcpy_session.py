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
    assert "i-frame-interval=2" in joined  # keyframe every ~2s for fast WebRTC first-frame
    assert "scid=1a" in joined
    # scrcpy-server 3.1's real option key is video_codec_options -- the
    # previous video_encoder_options name isn't recognized by this server
    # version at all (silently dropped server-side, "Unknown server option").
    assert "video_codec_options=" in joined
    assert "video_encoder_options=" not in joined
    # profile=1 (Baseline), level=512 (Level 3.1) -- a hint MediaCodec isn't
    # guaranteed to honor, but the only mitigation available for WebRTC H264
    # decoders that only support Level 3.1 profile-level-id variants.
    assert "profile=1" in joined
    assert "level=512" in joined


def test_build_scrcpy_args_uses_vbr():
    from server.scrcpy_session import build_scrcpy_args
    args = build_scrcpy_args("720", scid=0x1a)
    joined = " ".join(args)
    # bitrate-mode=1 is MediaFormat's KEY_BITRATE_MODE VBR value. It must be
    # appended to the SAME video_codec_options= token as the existing
    # i-frame-interval/profile/level settings -- scrcpy only accepts one such
    # argument; a second video_codec_options= (or the wrong key name
    # video_encoder_options=) is silently dropped server-side (see the
    # existing i-frame-interval regression test above this one).
    assert "video_codec_options=i-frame-interval=2,profile=1,level=512,bitrate-mode=1" in joined


def test_session_defaults_to_default_tier():
    from server.scrcpy_session import ScrcpySession
    from config import DEFAULT_TIER
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    assert s.tier == DEFAULT_TIER


def test_ffmpeg_args_copy_mux_no_reencode():
    # Copy-mux (no libx264). Keyframes are forced at the SOURCE via
    # ScrcpyControl.request_idr() (TYPE_RESET_VIDEO), which the reverted copy-mux
    # attempt never had — that is what makes copy-mux viable now without the
    # 20-30s rare-IDR black screen. No libx264 = no decode/encode CPU, ~1 frame
    # less latency.
    from server.scrcpy_session import build_ffmpeg_args
    args = build_ffmpeg_args("ffmpeg", "rtsp://localhost:8554/instance0", "720")
    assert args[args.index("-c:v") + 1] == "copy"
    assert "libx264" not in args
    # Encoder-side flags make no sense for copy and must be gone.
    assert "-g" not in args
    assert "-sc_threshold" not in args
    assert "-preset" not in args
    assert "-bufsize" not in args
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


def test_request_idr_sends_reset_video_byte():
    # TYPE_RESET_VIDEO = 0x11, a bodyless 1-byte control message. Sending it asks
    # scrcpy-server to make the device encoder emit an IDR keyframe on demand —
    # the mechanism that lets copy-mux start fast without a forced ffmpeg GOP.
    from server.scrcpy_session import ScrcpyControl
    c = ScrcpyControl(27183, "emulator-5554")
    sent = []
    c._send = lambda data: sent.append(data)
    c.request_idr()
    assert sent == [b"\x11"]


def test_set_tier_updates_tier_when_not_running():
    from server.scrcpy_session import ScrcpySession
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    assert s.set_tier("1080") is True
    assert s.tier == "1080"


class _LiveProc:
    """Stand-in ffmpeg proc that reports as running (poll() -> None)."""
    def poll(self):
        return None
    def kill(self):
        pass


def _force_alive(s):
    """Put a session into the real 'alive' state the property reads: running,
    a live ffmpeg proc, and a fresh frame heartbeat."""
    import time as _t
    s._running = True
    s._ffmpeg_proc = _LiveProc()
    s._last_frame_ts = _t.monotonic()


def test_restart_if_dead_skips_when_alive():
    # Watchdog must not restart a session that is alive — that would double-start
    # a session mid tier-change and corrupt the scrcpy handshake.
    from server.scrcpy_session import ScrcpySession
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    calls = {"start": 0, "stop": 0}
    s.start = lambda: calls.__setitem__("start", calls["start"] + 1) or True
    s.stop = lambda: calls.__setitem__("stop", calls["stop"] + 1)
    _force_alive(s)

    assert s.restart_if_dead() is True
    assert calls == {"start": 0, "stop": 0}  # no restart fired


def test_alive_false_when_ffmpeg_exited():
    # A crashed ffmpeg (poll() returns an exit code) means the RTSP publisher is
    # gone even though the finally-block cleanup has not run yet (the stream loop
    # may still be blocked on a socket read). alive must report dead so the
    # watchdog restarts it, instead of trusting the stale _ffmpeg_proc handle.
    from server.scrcpy_session import ScrcpySession
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)

    class _DeadProc:
        def poll(self):
            return 1  # exited with code 1
    s._running = True
    s._ffmpeg_proc = _DeadProc()
    import time as _t
    s._last_frame_ts = _t.monotonic()
    assert not s.alive


def test_alive_false_when_frames_stalled():
    # ffmpeg process still alive but no frames written for longer than the stall
    # timeout: the scrcpy video read blocked (settimeout(None)) after the RTSP
    # publish silently stalled overnight. alive must go False so the watchdog
    # restarts the publisher instead of reporting a zombie session as healthy.
    from server.scrcpy_session import ScrcpySession, _STALL_TIMEOUT
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    s._running = True
    s._ffmpeg_proc = _LiveProc()
    import time as _t
    s._last_frame_ts = _t.monotonic() - (_STALL_TIMEOUT + 1)
    assert not s.alive


def test_alive_true_when_frames_fresh():
    from server.scrcpy_session import ScrcpySession
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    _force_alive(s)
    assert s.alive


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


def test_build_ffmpeg_args_safe_flags_only():
    from server.scrcpy_session import build_ffmpeg_args
    args = build_ffmpeg_args("ffmpeg", "rtsp://localhost:8554/instance0")
    joined = " ".join(args)
    assert "-hide_banner" in joined
    assert "-muxdelay 0" in joined
    assert "-muxpreload 0" in joined
    # Mandatory -- do not remove (see docstring: first copy-mux failure,
    # commit 15a2d4e, was caused by removing this).
    assert "-use_wallclock_as_timestamps 1" in joined
    assert "-avoid_negative_ts make_zero" in joined
    # These starve the H.264 demuxer of SPS/PPS bytes and cause mediamtx to
    # time out the publish after ~10s -- confirmed by a real incident. Must
    # never be reintroduced regardless of what the optimization spec suggests.
    assert "-probesize" not in joined
    assert "-analyzeduration" not in joined
    assert "nobuffer" not in joined
    assert "low_delay" not in joined


def test_nalu_write_queue_drops_oldest_under_backpressure():
    from server.scrcpy_session import _NaluWriteQueue
    q = _NaluWriteQueue(maxsize=2)
    q.put(b"frame1")
    q.put(b"frame2")
    q.put(b"frame3")  # queue full -> drops frame1, keeps frame2+frame3
    assert q.dropped == 1
    assert q.get() == b"frame2"
    assert q.get() == b"frame3"


def test_nalu_write_queue_never_splits_a_nalu():
    """A dropped item is always one whole put() payload, never a partial write."""
    from server.scrcpy_session import _NaluWriteQueue
    q = _NaluWriteQueue(maxsize=1)
    whole_nalu = b"\x00\x00\x00\x01" + b"x" * 5000
    q.put(whole_nalu)
    q.put(b"\x00\x00\x00\x01next")
    got = q.get()
    assert got == b"\x00\x00\x00\x01next"
    assert len(got) == len(b"\x00\x00\x00\x01next")  # whole, not truncated


def test_nalu_write_queue_close_unblocks_get():
    from server.scrcpy_session import _NaluWriteQueue
    q = _NaluWriteQueue(maxsize=4)
    q.close()
    assert q.get() is None  # shutdown sentinel


def test_nalu_write_queue_close_when_full_doesnt_crash():
    """close() must not raise queue.Full even if the queue is already full at call time."""
    from server.scrcpy_session import _NaluWriteQueue
    q = _NaluWriteQueue(maxsize=2)
    q.put(b"frame1")
    q.put(b"frame2")
    # Queue is now full; close() must not crash when trying to inject the sentinel.
    # The key test is that this doesn't raise queue.Full.
    q.close()  # Should succeed without raising queue.Full
    # Verify we can drain the queue without blocking (just verify get() returns something)
    assert q.get() is not None  # First get() returns a frame
