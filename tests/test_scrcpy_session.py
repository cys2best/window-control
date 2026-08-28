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


def _force_alive(s):
    """Put a session into the real 'alive' state the property reads: running,
    a connected video socket (persistent half up), and a fresh frame
    heartbeat. Does NOT touch ffmpeg/video_active -- alive no longer depends
    on the on-demand half at all (see Task 2 of the on-demand-ingest plan)."""
    import time as _t
    s._running = True
    s._video_sock = object()
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


def test_alive_false_when_frames_stalled():
    # scrcpy-server connection still up but no frames drained for longer than
    # the stall timeout: the device read blocked (settimeout(None)) after the
    # connection silently stalled overnight. alive must go False so the
    # watchdog restarts the persistent half instead of reporting a zombie
    # session as healthy.
    from server.scrcpy_session import ScrcpySession, _STALL_TIMEOUT
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    s._running = True
    s._video_sock = object()
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


class _FakeFfmpegProc:
    """Stand-in for subprocess.Popen(ffmpeg) — records stdin writes, never
    actually runs a process. Constructor signature intentionally ignores its
    args so it can be swapped in via monkeypatch for subprocess.Popen."""
    def __init__(self, *a, **k):
        self.stdin = type("Stdin", (), {
            "write": lambda self, data: None,
            "flush": lambda self: None,
            "close": lambda self: None,
        })()
        self.stdout = None
        self.stderr = type("Stderr", (), {"read": lambda self: b""})()
        self._killed = False

    def poll(self):
        return None if not self._killed else 0

    def kill(self):
        self._killed = True


def test_video_active_false_before_start_video():
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    assert s.video_active is False


def test_video_active_true_for_aiortc_backed_session_with_no_ffmpeg():
    session = ScrcpySession("emulator-5554", 0, "rtsp://unused", 100, 200)
    session._running = True
    session._video_sock = object()  # truthy stand-in; start_video_aiortc only checks not-None
    ok = session.start_video_aiortc(on_frame=lambda nalu: None)
    assert ok is True
    assert session.video_active is True
    assert session._ffmpeg_proc is None  # confirms no ffmpeg was spawned
    session.stop_video_aiortc()
    assert session.video_active is False


def test_start_video_refuses_when_persistent_half_not_up():
    # start_video() must not spawn ffmpeg for a session whose scrcpy-server
    # video socket was never connected -- there is nothing for ffmpeg to
    # read from yet, and mediamtx's runOnDemandStartTimeout would just burn
    # its whole window waiting on a stream that can never arrive.
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    assert s.start_video() is False
    assert s.video_active is False


def test_start_video_spawns_ffmpeg_when_persistent_half_up(monkeypatch):
    import server.scrcpy_session as mod
    monkeypatch.setattr(mod.subprocess, "Popen", _FakeFfmpegProc)
    monkeypatch.setattr(mod, "_get_ffmpeg", lambda: "ffmpeg")

    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    # Simulate the persistent half already being up (Task's _persistent_loop
    # sets these once the scrcpy-server handshake completes).
    s._running = True
    s._video_sock = object()

    assert s.start_video() is True
    assert s.video_active is True


def test_start_video_idempotent_when_already_active(monkeypatch):
    import server.scrcpy_session as mod
    monkeypatch.setattr(mod.subprocess, "Popen", _FakeFfmpegProc)
    monkeypatch.setattr(mod, "_get_ffmpeg", lambda: "ffmpeg")

    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    s._running = True
    s._video_sock = object()
    assert s.start_video() is True
    first_proc = s._ffmpeg_proc
    assert s.start_video() is True  # second call is a no-op, not a second spawn
    assert s._ffmpeg_proc is first_proc


def test_stop_video_tears_down_ffmpeg_and_write_queue(monkeypatch):
    import server.scrcpy_session as mod
    monkeypatch.setattr(mod.subprocess, "Popen", _FakeFfmpegProc)
    monkeypatch.setattr(mod, "_get_ffmpeg", lambda: "ffmpeg")

    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    s._running = True
    s._video_sock = object()
    s.start_video()
    assert s.video_active is True

    s.stop_video()

    assert s.video_active is False
    assert s._write_queue is None
    assert s._writer_thread is None


def test_stop_video_noop_when_not_active():
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    s.stop_video()  # must not raise
    assert s.video_active is False


def test_start_video_concurrent_calls_dont_double_spawn(monkeypatch):
    # Two concurrent start_video() calls (e.g. mediamtx's runOnDemand "no one
    # publishing yet" window firing twice in quick succession) both pass the
    # entry check before either has committed, both spawn a full ffmpeg
    # process + writer thread, and race to commit. Exactly one must survive;
    # the loser's ffmpeg_proc must be killed and its write_queue closed, not
    # leaked (orphaned writer thread blocked forever on q.get()).
    import server.scrcpy_session as mod
    import threading

    created = []
    # Barrier forces both threads to finish constructing their ffmpeg stand-in
    # before either proceeds to the final atomic commit/re-check below --
    # without this, the race window is too small to hit reliably.
    barrier = threading.Barrier(2, timeout=5)

    class _SlowFakeProc(_FakeFfmpegProc):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            created.append(self)
            barrier.wait()

    monkeypatch.setattr(mod.subprocess, "Popen", _SlowFakeProc)
    monkeypatch.setattr(mod, "_get_ffmpeg", lambda: "ffmpeg")

    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    s._running = True
    s._video_sock = object()

    results = []

    def call():
        results.append(s.start_video())

    t1 = threading.Thread(target=call)
    t2 = threading.Thread(target=call)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert len(created) == 2
    assert results == [True, True]  # winner started it; loser sees it's active
    assert s.video_active is True
    alive = [p for p in created if not p._killed]
    killed = [p for p in created if p._killed]
    assert len(alive) == 1  # exactly one ffmpeg survives
    assert len(killed) == 1  # the loser was killed, not leaked
    assert s._ffmpeg_proc is alive[0]


def test_start_video_resurrection_after_teardown_is_prevented(monkeypatch):
    # A start_video() call already past its entry check must not resurrect
    # on-demand state after a concurrent stop() tears the persistent half
    # down -- otherwise nothing is left to ever clean the committed ffmpeg
    # process up (the persistent loop that would call stop_video() again has
    # already exited).
    import server.scrcpy_session as mod

    monkeypatch.setattr(mod.subprocess, "Popen", _FakeFfmpegProc)
    monkeypatch.setattr(mod, "_get_ffmpeg", lambda: "ffmpeg")

    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    s._running = True
    s._video_sock = object()

    # Simulate stop() having landed between the entry check and the final
    # commit by tearing down persistent-half state right before start_video()
    # re-checks it. We do this by monkeypatching subprocess.Popen to flip the
    # state as a side effect of being called -- standing in for a concurrent
    # stop() that ran in the window while ffmpeg was being spawned.
    def _popen_then_stop(*a, **k):
        proc = _FakeFfmpegProc(*a, **k)
        s._running = False
        s._video_sock = None
        return proc
    monkeypatch.setattr(mod.subprocess, "Popen", _popen_then_stop)

    assert s.start_video() is False
    assert s.video_active is False
    assert s._ffmpeg_proc is None


def test_start_video_respawns_when_previous_ffmpeg_died(monkeypatch):
    # A non-None but already-exited ffmpeg must not short-circuit the
    # idempotency check into reporting success -- mediamtx would then sit out
    # its whole runOnDemandStartTimeout waiting for a publisher that is never
    # coming back. The corpse gets reaped and a fresh ffmpeg spawned.
    import server.scrcpy_session as mod
    monkeypatch.setattr(mod.subprocess, "Popen", _FakeFfmpegProc)
    monkeypatch.setattr(mod, "_get_ffmpeg", lambda: "ffmpeg")

    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    s._running = True
    s._video_sock = object()

    assert s.start_video() is True
    first_proc = s._ffmpeg_proc
    first_proc._killed = True  # ffmpeg crashed: poll() now returns 0

    assert s.start_video() is True
    assert s._ffmpeg_proc is not first_proc
    assert s.video_active is True


# ── Generation counter: an outgoing persistent loop must not clobber the
#    session that replaced it (the restart path start()/restart_if_dead()/
#    set_tier() all share).


class _ExplodingSocket:
    """socket.socket stand-in that dies on construction, so _persistent_loop
    falls straight through to its finally block with no real device."""
    def __init__(self, *a, **k):
        raise RuntimeError("no scrcpy-server in tests")


def test_persistent_loop_stale_generation_does_not_clobber_running(monkeypatch):
    # THE C1 REGRESSION: start() sets _running=True and bumps the generation,
    # and only THEN joins the outgoing loop thread -- so the old thread's
    # finally typically runs during that join. If it isn't generation-guarded
    # it resets the brand-new session's _running back to False (and kills its
    # video), making every watchdog/tier-change restart produce a session that
    # instantly looks dead again.
    import server.scrcpy_session as mod
    monkeypatch.setattr(mod.socket, "socket", _ExplodingSocket)

    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    stopped = []
    s.stop_video = lambda: stopped.append(1)
    # State owned by generation 7 (the session that replaced us).
    s._running = True
    s._generation = 7

    s._persistent_loop(6)   # generation 6: superseded, must keep its hands off

    assert s._running is True
    assert stopped == []


def test_persistent_loop_current_generation_still_cleans_up(monkeypatch):
    # The mirror of the test above: the guard must not over-fire. A loop that
    # is still the CURRENT generation owns the teardown as before.
    import server.scrcpy_session as mod
    monkeypatch.setattr(mod.socket, "socket", _ExplodingSocket)

    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    stopped = []
    s.stop_video = lambda: stopped.append(1)
    s._running = True
    s._generation = 7

    s._persistent_loop(7)

    assert s._running is False
    assert stopped == [1]


def test_start_over_a_running_loop_keeps_new_session_running(monkeypatch):
    """End-to-end restart path: start() called while a previous
    _persistent_loop thread is still live. The outgoing thread is released
    mid-join (exactly when the real race happens) and must leave the new
    session's _running/_generation alone."""
    import server.scrcpy_session as mod
    import threading as _th

    monkeypatch.setattr(mod, "_find_adb", lambda: "adb")
    monkeypatch.setattr(mod, "_get_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(mod, "_start_server", lambda *a, **k: True)

    first_sock_gate = _th.Event()   # releases the OLD loop, mid-join
    second_sock_gate = _th.Event()  # keeps the NEW loop alive until asserted
    seen = []

    class _GatedSocket:
        def __init__(self, *a, **k):
            seen.append(1)
            if len(seen) == 1:
                first_sock_gate.wait(5)
            else:
                second_sock_gate.wait(5)
            raise RuntimeError("no scrcpy-server in tests")

    monkeypatch.setattr(mod.socket, "socket", _GatedSocket)

    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    assert s.start() is True
    old_thread = s._stream_thread
    assert s._generation == 1

    # Release the old loop 0.2s in, i.e. while start()'s join(timeout=5) is
    # blocked on it and after the new generation has already been claimed.
    _th.Timer(0.2, first_sock_gate.set).start()
    assert s.start() is True
    old_thread.join(timeout=5)
    assert not old_thread.is_alive()

    assert s._generation == 2
    assert s._stream_thread is not old_thread
    assert s._running is True          # NOT clobbered by generation 1's finally

    second_sock_gate.set()
    s._stream_thread.join(timeout=5)


# ── Persistent (video-independent) IDR heartbeat


def test_persistent_heartbeat_exits_when_generation_changes(monkeypatch):
    import server.scrcpy_session as mod
    sleeps = []
    monkeypatch.setattr(mod.time, "sleep", lambda d: sleeps.append(d))

    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    s._running = True
    s._generation = 1

    pokes = []

    def _poke():
        pokes.append(1)
        if len(pokes) == 3:
            s._generation = 2   # a restart superseded this heartbeat
    s.control.request_idr = _poke

    s._persistent_heartbeat(1)   # must return, not spin forever

    assert pokes == [1, 1, 1]
    assert sleeps == [8.0, 8.0, 8.0, 8.0]


def test_persistent_heartbeat_exits_when_session_stops(monkeypatch):
    import server.scrcpy_session as mod
    monkeypatch.setattr(mod.time, "sleep", lambda d: None)

    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    s._running = True
    s._generation = 1
    pokes = []

    def _poke():
        pokes.append(1)
        s._running = False
    s.control.request_idr = _poke

    s._persistent_heartbeat(1)
    assert pokes == [1]


def test_persistent_heartbeat_runs_independent_of_video(monkeypatch):
    # Regression guard for C2: the ONLY IDR source used to be the video-scoped
    # _idr_heartbeat, so an instance with no WHEP viewer sitting on a static
    # screen (Android's encoder emits nothing) tripped _STALL_TIMEOUT and
    # restarted every ~15-25s. The persistent heartbeat must be started from
    # _persistent_loop itself, not from start_video().
    import inspect
    from server.scrcpy_session import ScrcpySession
    loop_src = inspect.getsource(ScrcpySession._persistent_loop)
    assert "_persistent_heartbeat" in loop_src
    hb_src = inspect.getsource(ScrcpySession._persistent_heartbeat)
    # Generation-scoped, NOT ffmpeg-identity-scoped (that's the wrong lifetime).
    assert "_generation" in hb_src
    assert "_ffmpeg_proc" not in hb_src


# ── Video restore after a self-initiated restart


def test_restore_video_after_restart_polls_until_persistent_half_ready(monkeypatch):
    # start() returns as soon as the loop thread is spawned -- before the
    # handshake sets _video_sock -- so an immediate start_video() returns
    # False. The restore must keep trying instead of giving up on the first no.
    import server.scrcpy_session as mod
    monkeypatch.setattr(mod.time, "sleep", lambda d: None)

    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    attempts = []

    def _start_video():
        attempts.append(1)
        return len(attempts) >= 3
    s.start_video = _start_video

    s._restore_video_after_restart()
    assert len(attempts) == 3


def test_restart_if_dead_restores_video_when_a_viewer_was_watching():
    # _persistent_loop's finally calls stop_video(), and mediamtx only re-fires
    # runOnDemand for a reader count going 0->1 -- so without this the viewer
    # watching a watchdog-restarted instance loses video permanently.
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    # video_active (read by restart_if_dead below) is backend-agnostic and
    # checks _write_queue, not _ffmpeg_proc -- set it directly to simulate
    # "a viewer is watching" without depending on which backend they're on.
    s._write_queue = object()
    s._video_sock = None                 # ...but the persistent half is dead
    calls = {"start": 0, "restore": 0}
    s.stop = lambda: None
    s.start = lambda: calls.__setitem__("start", calls["start"] + 1) or True
    s._restore_video_after_restart = lambda: calls.__setitem__("restore", calls["restore"] + 1)

    assert s.restart_if_dead() is True
    assert calls == {"start": 1, "restore": 1}


def test_restart_if_dead_does_not_restore_video_when_nobody_was_watching():
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    calls = {"start": 0, "restore": 0}
    s.stop = lambda: None
    s.start = lambda: calls.__setitem__("start", calls["start"] + 1) or True
    s._restore_video_after_restart = lambda: calls.__setitem__("restore", calls["restore"] + 1)

    assert s.restart_if_dead() is True
    assert calls == {"start": 1, "restore": 0}


def test_set_tier_restarts_even_with_no_viewer_watching():
    # Post-split, gating the relaunch on `_ffmpeg_proc is not None` would mean
    # "only re-launch scrcpy-server if someone is watching" -- an unwatched
    # instance would keep encoding at the OLD resolution/bitrate while set_tier
    # still reported success.
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    s._running = True
    s._ffmpeg_proc = None   # persistent half up, no viewer
    calls = {"start": 0, "restore": 0}
    s.stop = lambda: None
    s.start = lambda: calls.__setitem__("start", calls["start"] + 1) or True
    s._restore_video_after_restart = lambda: calls.__setitem__("restore", calls["restore"] + 1)

    assert s.set_tier("1080") is True
    assert s.tier == "1080"
    assert calls == {"start": 1, "restore": 0}


def test_set_tier_restores_video_when_a_viewer_was_watching():
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    s._running = True
    # was_video_active (read by set_tier below) is backend-agnostic and
    # checks _write_queue, not _ffmpeg_proc -- set it directly to simulate
    # "a viewer is watching" without depending on which backend they're on.
    s._write_queue = object()
    calls = {"start": 0, "restore": 0}
    s.stop = lambda: None
    s.start = lambda: calls.__setitem__("start", calls["start"] + 1) or True
    s._restore_video_after_restart = lambda: calls.__setitem__("restore", calls["restore"] + 1)

    assert s.set_tier("1080") is True
    assert calls == {"start": 1, "restore": 1}


def test_set_tier_restores_video_via_aiortc_when_an_aiortc_viewer_was_watching():
    # video_active's underlying field (_write_queue) is backend-agnostic, but
    # set_tier's restore path delegates to _restore_video_after_restart --
    # this confirms an aiortc viewer's video comes back via
    # start_video_aiortc (not start_video, the ffmpeg entry point) after a
    # tier-change restart. Runs the real _restore_video_after_restart (not
    # mocked away, unlike the sibling test above) so this exercises the
    # actual on_frame dispatch, not just that "some restore" happened.
    s = ScrcpySession("emulator-5554", 0, "rtsp://localhost:8554/instance0", 720, 1280)
    s._running = True
    s._write_queue = object()          # an aiortc viewer is watching
    s._aiortc_on_frame = lambda nalu: None
    calls = {"start": 0, "start_video": 0, "start_video_aiortc": 0}
    s.stop = lambda: None
    s.start = lambda: calls.__setitem__("start", calls["start"] + 1) or True
    s.start_video = lambda: calls.__setitem__("start_video", calls["start_video"] + 1) or True
    s.start_video_aiortc = lambda on_frame: (
        calls.__setitem__("start_video_aiortc", calls["start_video_aiortc"] + 1) or True
    )

    assert s.set_tier("1080") is True
    assert calls == {"start": 1, "start_video": 0, "start_video_aiortc": 1}


def test_idr_heartbeat_steady_state_interval_is_8s():
    import inspect
    from server.scrcpy_session import ScrcpySession
    src = inspect.getsource(ScrcpySession._idr_heartbeat)
    # Steady-state interval backed off from 2.0s to 8.0s -- the heartbeat is
    # kept (it's a load-bearing encoder keep-alive during static screens,
    # see project_copy_mux_idr), just made less frequent to cut its bitrate
    # tax. The dense early-burst window (0.4s cadence, first 4s) is unchanged.
    assert "else 8.0" in src
    assert "else 2.0" not in src
