"""
Socket-free scrcpy-server launcher: ADB forward + server process management.

Separated from ScrcpySession (which owns video/control sockets) to support
engine.exe orchestration that needs to launch scrcpy-server without touching
Python's own media sockets.
"""

import os
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass
from typing import Callable

from config import ASSETS_DIR, QUALITY_TIERS, DEFAULT_TIER

_SCRCPY_BASE_PORT = 27183   # instance 0 → 27183, instance 1 → 27184, …
_SERVER_JAR = "scrcpy-server"  # filename in assets/scrcpy/


def _log(msg: str):
    for _p in [r"C:\ProgramData\WindowControl", r"C:\Windows\Temp"]:
        try:
            os.makedirs(_p, exist_ok=True)
            with open(os.path.join(_p, "service_crash.log"), "a") as f:
                f.write(msg + "\n")
            return
        except Exception:
            continue


def no_window_flags():
    if sys.platform == "win32":
        return {"creationflags": 0x08000000}
    return {}


def find_adb() -> str | None:
    from server.adb_manager import _find_adb as _adb
    return _adb()


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


def start_server(adb: str, serial: str, port: int, scid: int, tier: str) -> bool:
    """Push server jar, launch it, set up ONE adb forward.

    With tunnel_forward=true, scrcpy-server opens a single LocalServerSocket and
    accepts connections in order: video first, then control. Both go to the same
    abstract socket — one adb forward covers both.
    """
    nw = no_window_flags()
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
        _log(f"[scrcpy] start_server error serial={serial}: {traceback.format_exc()[:400]}")
        return False


def stop_server(adb: str, serial: str, port: int, scid: int) -> None:
    nw = no_window_flags()
    subprocess.run(
        [adb, "-s", serial, "shell", f"pkill -f 'scrcpy-server.*scid={scid:x}'"],
        capture_output=True, timeout=5, **nw,
    )
    subprocess.run(
        [adb, "-s", serial, "forward", "--remove", f"tcp:{port}"],
        capture_output=True, timeout=5, **nw,
    )


@dataclass(frozen=True)
class ScrcpyLaunch:
    port: int
    generation: int
    tier: str


class ScrcpyServerLauncher:
    """Socket-free scrcpy-server launcher: manages ADB forward and server process.

    Does not open any Python sockets or check ffmpeg — only coordinates the
    Android server process and ADB port forwarding. The video/control socket
    connection is the caller's responsibility.
    """

    def __init__(self, serial: str, instance_index: int,
                 find_adb: Callable[[], str | None] = find_adb,
                 start_server: Callable[[str, str, int, int, str], bool] = start_server,
                 stop_server: Callable[[str, str, int, int], None] = stop_server):
        self._serial = serial
        self._instance_index = instance_index
        self._tcp_port = _SCRCPY_BASE_PORT + instance_index
        self._find_adb = find_adb
        self._start_server = start_server
        self._stop_server = stop_server
        self._last_launch: ScrcpyLaunch | None = None

    def launch(self, tier: str, generation: int) -> ScrcpyLaunch:
        """Launch scrcpy-server with the given tier and generation.

        Validates tier first, then starts the server via ADB.

        Args:
            tier: Quality tier name (must be in QUALITY_TIERS).
            generation: Non-negative generation counter for restart tracking.

        Returns:
            ScrcpyLaunch with port, generation, and tier.

        Raises:
            ValueError: Unknown quality tier.
            RuntimeError: ADB not found or start_server failed.
        """
        if tier not in QUALITY_TIERS:
            raise ValueError(f"unknown quality tier: {tier}")
        if generation < 0:
            raise ValueError(f"generation must be non-negative, got {generation}")

        adb = self._find_adb()
        if not adb:
            raise RuntimeError("adb not found")

        if not self._start_server(adb, self._serial, self._tcp_port, self._instance_index, tier):
            raise RuntimeError(f"failed to start server for {self._serial}")

        self._last_launch = ScrcpyLaunch(
            port=self._tcp_port,
            generation=generation,
            tier=tier,
        )
        return self._last_launch

    def stop(self) -> None:
        """Stop the server and remove its ADB forward.

        Idempotent: can be called multiple times safely.
        """
        if self._last_launch is None:
            return

        adb = self._find_adb()
        if adb:
            self._stop_server(adb, self._serial, self._tcp_port, self._instance_index)
        self._last_launch = None
