"""
ScrcpySession: captures H.264 from LDPlayer via scrcpy-server TCP protocol,
and sends touch/key input via the scrcpy control socket.

Setup (per instance, done by _start_server()):
  adb push scrcpy-server /data/local/tmp/scrcpy-server.jar
  adb shell CLASSPATH=... app_process / com.genymobile.scrcpy.Server 3.1 \
      tunnel_forward=true video_codec=h264 max_fps=30 bit_rate=4000000 \
      send_device_meta=true send_frame_meta=true control=true audio=false &
  adb forward tcp:<port> localabstract:scrcpy_<scid>   (ONE forward — video+control share it)

Protocol (scrcpy-server 3.x, tunnel_forward=true, control=true, audio=false):
  Connect order: video socket first, then control socket (server expects this order).
  Video socket:
    1. Read 1-byte dummy
    2. Read 64-byte device name (zero-padded UTF-8)
    3. Read 4-byte codec_id (big-endian uint32)
    4. Read 8-byte video size: 4+4 width/height
    5. Frame loop: 12-byte header (8 pts_flags + 4 size) + payload
  Control socket (second TCP connection to same forwarded port):
    No handshake — send control messages directly.

Control message format (scrcpy 3.x):
  INJECT_TOUCH_EVENT (type=0x02), 32 bytes total:
    [0]     u8  type=0x02
    [1]     u8  action (0=down, 1=up, 2=move)
    [2-9]   i64 pointerId (big-endian, use 0 for single touch)
    [10-13] i32 x (big-endian, pixel coords)
    [14-17] i32 y (big-endian, pixel coords)
    [18-19] u16 screenWidth  (big-endian)
    [20-21] u16 screenHeight (big-endian)
    [22-23] u16 pressure (big-endian, 0xffff=1.0 pressed, 0=released)
    [24-27] u32 actionButton (big-endian, 0 for touch)
    [28-31] u32 buttons (big-endian, 0 for touch)  ← total 32 bytes per scrcpy 3.x
  INJECT_KEYCODE (type=0x00), 14 bytes total:
    [0]     u8  type=0x00
    [1]     u8  action (0=down, 1=up)
    [2-5]   i32 keycode (big-endian, Android KeyEvent keycode)
    [6-9]   i32 repeat (big-endian, 0)
    [10-13] i32 metaState (big-endian, 0)

Pipeline per instance:
  Video:   scrcpy-server → TCP → Python → ffmpeg stdin → RTSP → mediamtx → WHEP
  Control: WebSocket handler → ScrcpyControl.send_touch() → TCP → scrcpy-server → Android
"""

import os
import queue
import socket
import struct
import subprocess
import sys
import threading
import time
import traceback

from config import ASSETS_DIR, QUALITY_TIERS, DEFAULT_TIER

_SCRCPY_BASE_PORT = 27183   # instance 0 → 27183, instance 1 → 27184, …

# A session is considered stalled (and therefore not `alive`) if no video frame
# has been written to ffmpeg for this many seconds. Overnight, the RTSP publish
# can silently stall — mediamtx times out the RTSP session ('i/o timeout … not
# in use') while the scrcpy video read still blocks forever — leaving a zombie
# session that reported alive and was never restarted. This heartbeat catches
# that. scrcpy pushes frames continuously (even a static screen re-sends), so a
# multi-second gap means the pipeline is dead, not idle.
_STALL_TIMEOUT = 15.0
_SERVER_JAR = "scrcpy-server"  # filename in assets/scrcpy/

_WRITE_QUEUE_DEPTH = 30  # ~1s of frames at 30fps


class _NaluWriteQueue:
    """Bounded queue of whole NAL units feeding a dedicated ffmpeg-stdin writer.

    Decouples the video-read thread from ffmpeg's stdin: if the RTSP consumer
    (mediamtx) stalls and ffmpeg's stdin pipe fills, the OLD code's direct
    `stdin.write()` in the read loop would block the video-read thread right
    along with it, eventually tripping `_STALL_TIMEOUT` and forcing a full
    session restart. This queue instead drops the OLDEST whole NAL under
    backpressure and keeps draining the scrcpy socket, so a transient stall
    degrades to dropped frames (repaired by the next IDR) instead of a
    restart. Never split a NAL when dropping -- a partial NAL corrupts the
    decoder until the next IDR.
    """

    def __init__(self, maxsize: int = _WRITE_QUEUE_DEPTH):
        self._q: "queue.Queue[bytes | None]" = queue.Queue(maxsize=maxsize)
        self.dropped = 0

    def put(self, nalu: bytes) -> None:
        try:
            self._q.put_nowait(nalu)
            return
        except queue.Full:
            pass
        try:
            self._q.get_nowait()
            self.dropped += 1
        except queue.Empty:
            pass
        try:
            self._q.put_nowait(nalu)
        except queue.Full:
            self.dropped += 1

    def get(self) -> bytes | None:
        return self._q.get()

    def close(self) -> None:
        """Unblock a thread waiting in get() with a shutdown sentinel."""
        try:
            self._q.put_nowait(None)
            return
        except queue.Full:
            pass
        try:
            self._q.get_nowait()
        except queue.Empty:
            pass
        try:
            self._q.put_nowait(None)
        except queue.Full:
            pass

    def qsize(self) -> int:
        return self._q.qsize()

    @property
    def full(self) -> bool:
        return self._q.full()

def _log(msg: str):
    for _p in [r"C:\ProgramData\WindowControl", r"C:\Windows\Temp"]:
        try:
            os.makedirs(_p, exist_ok=True)
            with open(os.path.join(_p, "service_crash.log"), "a") as f:
                f.write(msg + "\n")
            return
        except Exception:
            continue


def _no_window_flags():
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}
    return {}


def _find_adb() -> str | None:
    from server.adb_manager import _find_adb as _adb
    return _adb()


def _get_ffmpeg() -> str | None:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _server_jar_path() -> str:
    return os.path.join(ASSETS_DIR, "scrcpy", _SERVER_JAR)


def build_scrcpy_args(tier: str, scid: int) -> list[str]:
    """Build scrcpy-server arguments from a quality tier.

    Returns the app_process arg tokens (the part after `com.genymobile.scrcpy.Server 3.1`).
    Includes max_size, bit_rate, max_fps from the tier, plus video_encoder_options and scid.
    """
    t = QUALITY_TIERS.get(tier, QUALITY_TIERS[DEFAULT_TIER])
    return [
        "tunnel_forward=true",
        "video_codec=h264",
        f"max_size={t['max_size']}",
        f"bit_rate={t['bit_rate']}",
        f"max_fps={t['max_fps']}",
        "send_device_meta=true",
        "send_frame_meta=true",
        "control=true",
        "audio=false",
        # Keyframe cadence + H264 profile/level hints for MediaCodec.
        #
        # `video_codec_options` is scrcpy-server 3.1's real option key (confirmed
        # against the bundled server's decompiled source) — the previous
        # `video_encoder_options` name is not recognized by this server version
        # at all; it's silently logged as "Unknown server option" and dropped,
        # meaning i-frame-interval was never actually applied. The IDR heartbeat
        # (ScrcpyControl.request_idr(), called on a ~2s cadence by callers) has
        # been masking the resulting lack of keyframe cadence control.
        #
        # profile=1,level=512 requests H264 Baseline + Level 3.1
        # (MediaCodecInfo.CodecProfileLevel.AVCProfileBaseline=0x01,
        # AVCLevel31=0x200=512). This is a HINT, not a guarantee — MediaCodec's
        # own docs state the encoder is free to pick a different, compatible
        # level if the configured resolution/bitrate/fps dictate it (confirmed:
        # this device was observed emitting Level 4.1 output for identical
        # max_size/bit_rate/max_fps settings on a different run). Requesting
        # Level 3.1 explicitly is still worth doing since browsers' WebRTC H264
        # decoders only advertise Level 3.1 variants (profile-level-id ending in
        # "1f") — Level 4.1 output cannot be negotiated by any WebRTC-based
        # client at all, so this is the best available mitigation even though
        # it isn't hard-enforced. 720p@30fps@4Mbps fits Level 3.1's ceiling
        # (MaxFS=3600 MB, MaxMBPS=108000 MB/s — both exactly met at 1280x720@30,
        # zero headroom but spec-compliant; MaxBR=14Mbps, well under 4Mbps).
        "video_codec_options=i-frame-interval=2,profile=1,level=512,bitrate-mode=1",
        f"scid={scid:x}",
    ]


def build_ffmpeg_args(ffmpeg_exe: str, rtsp_url: str, tier: str = DEFAULT_TIER) -> list[str]:
    """Build ffmpeg arguments to mux scrcpy H.264 -> RTSP with NO re-encode.

    Copy-mux (`-c:v copy`): the device already emits H.264 at the tier's
    bitrate/fps, so re-encoding with libx264 only burned CPU and added ~1
    frame of latency.

    `-use_wallclock_as_timestamps 1` is mandatory and must stay: raw H.264 from
    scrcpy carries no container timestamps, so without it ffmpeg guesses 25fps,
    the RTSP muxer stalls on non-monotonic DTS, and mediamtx times out the
    publish (~10s 'i/o timeout'), dropping every instance. This was the FIRST
    copy-mux failure (commit 15a2d4e); do not remove it.

    Args:
        ffmpeg_exe: Path to ffmpeg executable.
        rtsp_url: RTSP destination URL.
        tier: Quality tier -- accepted for signature compatibility; copy-mux
            carries whatever bitrate/fps the device already encoded.

    Returns:
        Full ffmpeg argv for streaming via mediamtx.
    """
    return [
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel", "warning",
        # NOTE: do NOT add -probesize 32 / -analyzeduration 0 / -fflags
        # nobuffer / -flags low_delay here. They starve ffmpeg's h264
        # demuxer of the bytes it needs to parse SPS/PPS, so it never emits
        # a valid stream -- mediamtx sees the publish stall and times it out
        # after ~10s ('i/o timeout'), dropping every instance. This was
        # tried and reverted; a later optimization pass suggested them again
        # from an external spec that did not know about this incident --
        # see docs/scrcpy-whep-optimization-spec.md's Task 1.1 and this
        # plan's "Deviations from the spec" section.
        "-use_wallclock_as_timestamps", "1",
        "-f", "h264",
        "-i", "pipe:0",
        "-c:v", "copy",
        "-avoid_negative_ts", "make_zero",
        "-f", "rtsp",
        "-rtsp_transport", "tcp",
        "-muxdelay", "0",
        "-muxpreload", "0",
        rtsp_url,
    ]


def _recvall(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


def _start_server(adb: str, serial: str, port: int, scid: int, tier: str) -> bool:
    """Push server jar, launch it, set up ONE adb forward.

    With tunnel_forward=true, scrcpy-server opens a single LocalServerSocket and
    accepts connections in order: video first, then control. Both go to the same
    abstract socket — one adb forward covers both.
    """
    nw = _no_window_flags()
    jar = _server_jar_path()
    if not os.path.exists(jar):
        _log(f"[scrcpy] server jar not found: {jar}")
        return False

    socket_name = f"scrcpy_{scid:08x}"

    try:
        subprocess.run(
            [adb, "-s", serial, "push", jar, "/data/local/tmp/scrcpy-server.jar"],
            capture_output=True, timeout=15, **nw,
        )
        subprocess.run(
            [adb, "-s", serial, "shell", f"pkill -f 'scrcpy-server.*scid={scid:x}'"],
            capture_output=True, timeout=5, **nw,
        )
        time.sleep(0.3)
        subprocess.Popen(
            [
                adb, "-s", serial, "shell",
                "CLASSPATH=/data/local/tmp/scrcpy-server.jar"
                " app_process / com.genymobile.scrcpy.Server 3.1 "
                + " ".join(build_scrcpy_args(tier, scid)),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **nw,
        )
        # adb forward just creates the local tunnel; it does not require the
        # server to be accepting yet (the video-connect retry in _persistent_loop
        # handles listen-readiness). A short settle is enough for app_process to
        # have spawned; the old 0.5s was padding.
        time.sleep(0.15)
        result = subprocess.run(
            [adb, "-s", serial, "forward", f"tcp:{port}", f"localabstract:{socket_name}"],
            capture_output=True, timeout=5, **nw,
        )
        if result.returncode != 0:
            _log(f"[scrcpy] forward failed serial={serial}: {result.stderr.decode()[:200]}")
            return False
        _log(f"[scrcpy] server ready serial={serial} scid={scid} port={port} socket={socket_name}")
        return True
    except Exception:
        _log(f"[scrcpy] _start_server error serial={serial}: {traceback.format_exc()[:400]}")
        return False


class ScrcpyControl:
    """Sends touch and key events to scrcpy-server via the control socket.

    The control socket is a persistent TCP connection to the same forwarded port
    as the video socket. scrcpy-server accepts connections in order: video first,
    then control. ScrcpySession connects video first, waits for the header, then
    ScrcpyControl connects second.

    Thread-safe: send() acquires a lock before writing.
    """

    # Action constants (Android MotionEvent)
    ACTION_DOWN = 0
    ACTION_UP   = 1
    ACTION_MOVE = 2

    # Android keycodes used for back/home/menu
    KEYCODE_BACK   = 4
    KEYCODE_HOME   = 3
    KEYCODE_MENU   = 82
    KEYCODE_VOLUME_UP   = 24
    KEYCODE_VOLUME_DOWN = 25

    def __init__(self, control_port: int, serial: str):
        self._port = control_port
        self._serial = serial
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()

    def connect(self) -> bool:
        """Open control socket. Call after video socket handshake completes."""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(5)
            sock.connect(("127.0.0.1", self._port))
            sock.settimeout(None)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            with self._lock:
                self._sock = sock
            _log(f"[control] connected serial={self._serial} port={self._port}")
            return True
        except Exception:
            _log(f"[control] connect failed serial={self._serial}: {traceback.format_exc()[:200]}")
            return False

    def send_touch(self, action: int, nx: float, ny: float, w: int, h: int,
                   pointer_id: int = 0):
        """Send INJECT_TOUCH_EVENT. nx/ny are normalized [0,1] coords."""
        x = int(nx * w)
        y = int(ny * h)
        pressure = 0xffff if action != self.ACTION_UP else 0
        # 32-byte INJECT_TOUCH_EVENT (scrcpy 3.x)
        msg = struct.pack(">BBQiiHHHII",
            0x02,           # type: INJECT_TOUCH_EVENT
            action,         # action (u8)
            pointer_id,     # pointerId (u64)
            x, y,           # x, y (i32 each)
            w & 0xffff,     # screenWidth (u16)
            h & 0xffff,     # screenHeight (u16)
            pressure,       # pressure (u16, 0xffff = 1.0)
            0,              # actionButton (u32)
            0,              # buttons (u32)
        )
        self._send(msg)

    def send_keycode(self, keycode: int):
        """Send INJECT_KEYCODE down+up for a single Android keycode."""
        for action in (0, 1):  # down, up
            msg = struct.pack(">BBiii",
                0x00,       # type: INJECT_KEYCODE
                action,     # action
                keycode,    # keycode
                0,          # repeat
                0,          # metaState
            )
            self._send(msg)

    def request_idr(self):
        """Ask scrcpy-server to emit an IDR keyframe now (TYPE_RESET_VIDEO=0x11).

        Bodyless 1-byte control message. With copy-mux there is no ffmpeg GOP to
        force keyframes, and some device MediaCodec encoders ignore
        i-frame-interval (emit IDR only every 20-30s → 20-30s WebRTC black
        screen). This drives an IDR on demand from the source, so first-frame and
        instance switch stay fast without re-encoding.
        """
        self._send(b"\x11")

    def _send(self, data: bytes):
        with self._lock:
            if self._sock is None:
                return
            try:
                self._sock.sendall(data)
            except Exception:
                _log(f"[control] send error serial={self._serial}, reconnecting")
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None

    def close(self):
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except Exception:
                    pass
                self._sock = None

    @property
    def connected(self) -> bool:
        with self._lock:
            return self._sock is not None


class ScrcpySession:
    """Manages scrcpy-server capture + ffmpeg RTSP push for one LDPlayer instance."""

    def __init__(self, serial: str, instance_index: int, rtsp_url: str,
                 w: int, h: int, tier: str = DEFAULT_TIER):
        self.serial = serial
        self.instance_index = instance_index
        self.rtsp_url = rtsp_url
        self.w = w
        self.h = h
        self.tier = tier
        self._tcp_port = _SCRCPY_BASE_PORT + instance_index
        self._ffmpeg_proc: subprocess.Popen | None = None
        self._stream_thread: threading.Thread | None = None
        self._video_sock: socket.socket | None = None
        self._running = False
        # Monotonic timestamp of the last frame written to ffmpeg. Drives the
        # stall detection in `alive` (see _STALL_TIMEOUT). 0.0 until first frame.
        self._last_frame_ts = 0.0
        self._lock = threading.Lock()
        # Bounded NAL-unit queue feeding a dedicated ffmpeg-stdin writer thread
        # (see _NaluWriteQueue). Set by start_video(), cleared by stop_video();
        # None when no viewer is watching (video not active).
        self._write_queue: _NaluWriteQueue | None = None
        self._writer_thread: threading.Thread | None = None
        # Serializes a full stop→start cycle so a tier-change restart and the
        # watchdog's dead-session restart can't interleave. Without it, two
        # start() calls race two _persistent_loop threads onto the same scrcpy TCP
        # port; scrcpy-server accepts one and the other reads a truncated
        # handshake ('struct.error: unpack requires a buffer of 4 bytes').
        self._restart_lock = threading.RLock()
        # Control socket connects to same port as video — server accepts both sequentially
        self.control = ScrcpyControl(self._tcp_port, serial)

    def start(self) -> bool:
        adb = _find_adb()
        if not adb:
            _log(f"[scrcpy] adb not found serial={self.serial}")
            return False
        if not _get_ffmpeg():
            _log(f"[scrcpy] ffmpeg not found serial={self.serial}")
            return False

        # Hold the restart lock across the WHOLE cycle: stop old, launch server,
        # spawn the new stream thread. A concurrent start() (watchdog vs. tier
        # change) blocks here instead of racing a second _persistent_loop onto the
        # same TCP port and corrupting the handshake.
        with self._restart_lock:
            with self._lock:
                self._stop_locked()
                self._running = True
                old_thread = self._stream_thread

            # Wait for the PREVIOUS stream thread to fully exit before launching a
            # new scrcpy-server on the same TCP port. _stop_locked signals it
            # (kills ffmpeg, shuts the video socket) but does not block; if we
            # raced ahead, the old thread would still hold/close the port while
            # the new server tries to bind it, and the new handshake reads 0
            # bytes ('handshake truncated'). Joining here is the actual fix the
            # restart lock alone did not provide.
            if old_thread is not None and old_thread is not threading.current_thread():
                old_thread.join(timeout=5)

            if not _start_server(adb, self.serial, self._tcp_port, self.instance_index, self.tier):
                with self._lock:
                    self._running = False
                return False

            # No blind wait here: the stream thread's FIRST video connect retries
            # on ECONNREFUSED until the server is actually accepting (see
            # _persistent_loop). A fixed sleep(1.0) both wasted time on fast devices
            # and was too short on slow ones; connect-retry is right either way.
            self._stream_thread = threading.Thread(target=self._persistent_loop, daemon=True)
            self._stream_thread.start()
            _log(f"[scrcpy] started serial={self.serial} port={self._tcp_port}")
            return True

    def _persistent_loop(self):
        """Persistent half: scrcpy-server handshake, control socket, and a
        drain loop that always reads frames off the device socket -- even
        when no viewer is watching -- so the scrcpy-server side never sees
        backpressure and the device connection itself stays healthy. Frames
        are forwarded to ffmpeg only while `start_video()` has set
        `self._write_queue`; otherwise they're read and discarded.

        Runs once per ScrcpySession.start() call, for the instance's whole
        life (InstanceManager brings this up at discovery, not per-viewer).
        The scrcpy-server accept order (video socket, then control socket)
        happens exactly once here -- there is no way to "re-open" just the
        video half later, which is why on-demand toggling happens at the
        ffmpeg layer, not by reconnecting this socket.
        """
        video_sock: socket.socket | None = None
        try:
            # scrcpy-server (tunnel_forward=true, control=true) accept order:
            #   1. accept video  → sends dummy byte immediately
            #   2. accept control (blocks here)
            #   3. sends device_meta + codec header on video socket
            # So: connect video, read dummy byte, connect control, then read the rest.
            # Retry the video connect until the server is accepting. This is the
            # readiness signal that replaces the old blind sleep(1.0) in start():
            # the FIRST connection scrcpy-server accepts IS the video socket, so
            # this keeps the accept-order semantics intact while only paying for
            # as much wait as the device actually needs. ECONNREFUSED = server not
            # listening yet; anything else propagates.
            _connect_deadline = time.monotonic() + 8.0
            while True:
                video_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                video_sock.settimeout(10)
                try:
                    video_sock.connect(("127.0.0.1", self._tcp_port))
                    break
                except (ConnectionRefusedError, OSError):
                    video_sock.close()
                    if not self._running or time.monotonic() >= _connect_deadline:
                        raise
                    time.sleep(0.05)
            with self._lock:
                self._video_sock = video_sock

            _recvall(video_sock, 1)   # dummy byte — sent right after video accept

            # Server now blocks on accept() for control socket.
            # Connect control to unblock it so it proceeds to send device_meta.
            self.control.connect()

            device_name = _recvall(video_sock, 64).rstrip(b"\x00").decode("utf-8", errors="replace")
            # A short read here means the video socket was closed mid-handshake —
            # usually because another start() grabbed this scrcpy port first.
            # Fail with a clear message instead of a raw struct.error.
            meta = _recvall(video_sock, 12)
            if len(meta) < 12:
                raise ConnectionError(
                    f"scrcpy handshake truncated ({len(meta)}/12 bytes) — "
                    f"port {self._tcp_port} likely taken by a concurrent start"
                )
            codec_id = struct.unpack(">I", meta[0:4])[0]
            init_w = struct.unpack(">I", meta[4:8])[0]
            init_h = struct.unpack(">I", meta[8:12])[0]
            _log(f"[scrcpy] handshake device={device_name!r} codec=0x{codec_id:08x} {init_w}x{init_h}")

            # Use actual frame dimensions from scrcpy handshake — may differ from wm size
            # if the device is rotated or LDPlayer reports a different logical resolution.
            # These are the dimensions scrcpy uses for coordinate mapping.
            self.w = init_w
            self.h = init_h

            # Cap the frame read so a silently-stalled scrcpy stream (device
            # frozen, RTSP publish wedged) breaks the loop and lets the session
            # go dead instead of blocking forever on _recvall. Longer than the
            # normal inter-frame gap; a real timeout here means the pipeline died.
            video_sock.settimeout(_STALL_TIMEOUT)
            self._last_frame_ts = time.monotonic()
            _log(f"[scrcpy] persistent half up serial={self.serial}")

            while self._running:
                header = _recvall(video_sock, 12)
                if len(header) < 12:
                    break
                size = struct.unpack(">I", header[8:12])[0]
                payload = _recvall(video_sock, size)
                if len(payload) < size:
                    break
                self._last_frame_ts = time.monotonic()
                with self._lock:
                    wq = self._write_queue
                if wq is not None:
                    wq.put(payload)

        except Exception:
            _log(f"[scrcpy] persistent_loop error serial={self.serial}: {traceback.format_exc()[:400]}")
        finally:
            with self._lock:
                if self._video_sock is video_sock:
                    self._video_sock = None
            if video_sock:
                try:
                    video_sock.close()
                except Exception:
                    pass
            self.stop_video()
            with self._lock:
                self._running = False
            _log(f"[scrcpy] persistent_loop exited serial={self.serial}")

    def _idr_heartbeat(self, ffmpeg_proc):
        """Request an IDR on a fast burst early, then settle to every ~8s.

        The early burst matters on a quality-change/switch: the client tears down
        and renegotiates WHEP, and whichever moment its new subscriber joins, it
        can only paint once a keyframe arrives. A 2s-only cadence leaves a join
        that lands just after the initial IDR waiting up to ~2s (visible freeze).
        For the first few seconds we poke every ~0.4s so a fresh WHEP join gets a
        keyframe almost immediately, then relax to ~8s for steady state.

        Identity-guarded on ffmpeg_proc: an old heartbeat thread from a session
        that was replaced by a tier-change/restart must not keep poking the new
        session's control socket. It exits as soon as _running drops or a
        different ffmpeg_proc has taken over.
        """
        started = time.monotonic()
        while self._running:
            # Dense early (switch window), sparse after — keeps steady-state
            # control traffic low without penalizing the reconnect.
            interval = 0.4 if (time.monotonic() - started) < 4.0 else 8.0
            time.sleep(interval)
            with self._lock:
                if not self._running or self._ffmpeg_proc is not ffmpeg_proc:
                    return
            self.control.request_idr()

    def stop(self):
        with self._lock:
            self._stop_locked()

    def _stop_locked(self):
        self._running = False
        # Unblock the _persistent_loop's _recvall by shutting down the video socket,
        # so the blocked read returns immediately instead of hanging (and leaking
        # the FD) until the next packet arrives.
        if self._video_sock is not None:
            try:
                self._video_sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
        self.control.close()
        _log(f"[scrcpy] stopped serial={self.serial}")

    def start_video(self) -> bool:
        """Start the on-demand half: ffmpeg + write queue + writer + IDR heartbeat.

        Requires the persistent half (scrcpy-server, video socket, handshake)
        to already be up -- called by InstanceManager.start_video() in
        response to mediamtx's runOnDemand hook, which only fires once a
        WHEP client actually requests the path, by which point discovery has
        long since brought the persistent half up. A no-op (returns True) if
        video is already active, so a duplicate runOnDemand call (e.g. two
        readers joining close together) can't double-spawn ffmpeg.
        """
        with self._lock:
            if not self._running or self._video_sock is None:
                return False
            if self._ffmpeg_proc is not None:
                return True
        ffmpeg_exe = _get_ffmpeg()
        if not ffmpeg_exe:
            _log(f"[scrcpy] start_video: ffmpeg not found serial={self.serial}")
            return False
        ffmpeg_proc = subprocess.Popen(
            build_ffmpeg_args(ffmpeg_exe, self.rtsp_url, self.tier),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            **_no_window_flags(),
        )
        write_queue = _NaluWriteQueue()

        def _writer_loop(proc=ffmpeg_proc, q=write_queue):
            while True:
                nalu = q.get()
                if nalu is None:
                    return
                try:
                    proc.stdin.write(nalu)
                    proc.stdin.flush()
                except Exception:
                    return

        writer_thread = threading.Thread(target=_writer_loop, daemon=True)
        writer_thread.start()
        idr_thread = threading.Thread(
            target=self._idr_heartbeat, args=(ffmpeg_proc,), daemon=True
        )
        idr_thread.start()
        self.control.request_idr()
        with self._lock:
            self._ffmpeg_proc = ffmpeg_proc
            self._write_queue = write_queue
            self._writer_thread = writer_thread
        _log(f"[scrcpy] video started serial={self.serial} -> {self.rtsp_url}")
        return True

    def stop_video(self) -> None:
        """Stop the on-demand half. No-op if not currently active.

        Called by InstanceManager.stop_video() in response to mediamtx's
        runOnUnDemand hook (fires runOnDemandCloseAfter seconds after the
        last reader disconnects). The persistent half (scrcpy-server, video
        socket, drain loop, control socket) is untouched -- input to this
        instance keeps working after this call.
        """
        with self._lock:
            ffmpeg_proc = self._ffmpeg_proc
            write_queue = self._write_queue
            self._ffmpeg_proc = None
            self._write_queue = None
            self._writer_thread = None
        if write_queue is not None:
            write_queue.close()
        if ffmpeg_proc is not None:
            try:
                ffmpeg_proc.stdin.close()
            except Exception:
                pass
            try:
                ffmpeg_proc.kill()
            except Exception:
                pass
            try:
                stderr_bytes = ffmpeg_proc.stderr.read()
                if stderr_bytes:
                    _log(f"[scrcpy] ffmpeg stderr serial={self.serial}: "
                         f"{stderr_bytes.decode('utf-8', errors='replace')[:600]}")
            except Exception:
                pass
        _log(f"[scrcpy] video stopped serial={self.serial}")

    @property
    def video_active(self) -> bool:
        with self._lock:
            return self._ffmpeg_proc is not None

    @property
    def alive(self) -> bool:
        with self._lock:
            if not self._running or self._ffmpeg_proc is None:
                return False
            # ffmpeg crashed but the stream loop's finally hasn't cleared the
            # handle yet (it may still be blocked on a socket read): treat as
            # dead so the watchdog restarts the publisher.
            poll = getattr(self._ffmpeg_proc, "poll", None)
            if poll is not None and poll() is not None:
                return False
            # No frames written for too long → publisher stalled (RTSP wedged,
            # device frozen). The process may still be up, but nothing is
            # reaching mediamtx, so the session is effectively dead.
            if self._last_frame_ts and \
                    time.monotonic() - self._last_frame_ts > _STALL_TIMEOUT:
                return False
            # The writer thread can die (e.g. ffmpeg's stdin pipe breaks) while
            # ffmpeg_proc itself lingers and the video-read loop keeps stamping
            # _last_frame_ts from the still-healthy device socket -- that read
            # heartbeat alone can't see a dead writer. Without this check,
            # `alive` would report True forever while frames silently drop
            # into a full, unconsumed queue and mediamtx sees "no one is
            # publishing".
            writer = self._writer_thread
            if writer is not None and not writer.is_alive():
                return False
            return True

    def restart_if_dead(self) -> bool:
        """Restart the session only if it is still dead under the restart lock.

        The watchdog calls this. Acquiring _restart_lock first means a tier-change
        restart already in flight completes before we look; the re-check of
        `alive` inside the lock then sees the freshly-started session and skips,
        so the watchdog never fires a second, racing start().

        Returns True if the session is alive (either already, or after restart).
        """
        with self._restart_lock:
            if self.alive:
                return True
            _log(f"[scrcpy] watchdog restart (dead) serial={self.serial}")
            self.stop()
            return self.start()

    def set_tier(self, tier: str) -> bool:
        """Update quality tier. Restarts capture if running.

        Args:
            tier: Quality tier name (must be in QUALITY_TIERS).

        Returns:
            True if tier was accepted/set, False if unknown tier.
        """
        if tier not in QUALITY_TIERS:
            return False
        if tier == self.tier:
            return True
        # Hold the restart lock across stop→start so the watchdog can't observe
        # the brief dead window mid-tier-change and fire its own restart — two
        # concurrent starts corrupt the scrcpy handshake. start() re-acquires
        # the same RLock (reentrant), so nesting is fine.
        with self._restart_lock:
            self.tier = tier
            with self._lock:
                was_running = self._running and self._ffmpeg_proc is not None
            if was_running:
                self.stop()
                if not self.start():
                    _log(f"[scrcpy] set_tier restart failed serial={self.serial} tier={tier}")
                    return False
        return True
