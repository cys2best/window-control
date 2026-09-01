"""
InstanceManager: auto-discovers LDPlayer ADB devices, manages one ScrcpySession
per instance, and exposes the active session for input routing.

Auto-restart: a watchdog thread checks all sessions every 10s and restarts
any that have crashed.
"""

import threading
import time
import traceback
from typing import TYPE_CHECKING

from config import DEFAULT_TIER
from server import adb_manager
from server.engine_runtime import EngineSelection
from server.scrcpy_session import ScrcpySession
from server.mediamtx_manager import MediamtxManager
from server.webrtc_manager import WebrtcManager
from server.stun_server import StunServer

if TYPE_CHECKING:
    from server.engine_orchestrator import EngineOrchestrator


def _log(msg: str):
    import os
    for _p in [r"C:\ProgramData\WindowControl", r"C:\Windows\Temp"]:
        try:
            os.makedirs(_p, exist_ok=True)
            with open(os.path.join(_p, "service_crash.log"), "a") as f:
                f.write(msg + "\n")
            return
        except Exception:
            continue


def instance_name(serial: str) -> str:
    """Stable mediamtx path name from an ADB serial, e.g. 'instance0'."""
    import re
    m = re.match(r"emulator-(\d+)", serial)
    if m:
        port = int(m.group(1))
        idx = (port - 5554) // 2
        return f"instance{idx}"
    return f"instance_{serial.replace(':', '_')}"


class Instance:
    def __init__(self, vm: dict, session: ScrcpySession | None, w: int, h: int):
        self.id = vm["id"]               # "adb:SERIAL"
        self.serial = vm["id"][4:]       # "SERIAL"
        self.title = vm["title"]
        self.ldplayer_index = vm["ldplayer_index"]
        self.name = instance_name(self.serial)
        self.w = w
        self.h = h
        self.session = session
        self.tier = DEFAULT_TIER


class InstanceManager:
    def __init__(self, mediamtx: "MediamtxManager | None",
                 webrtc: "WebrtcManager | None" = None,
                 engine_orchestrator: "EngineOrchestrator | None" = None):
        self._mediamtx = mediamtx
        self._webrtc = webrtc
        self._engine_orchestrator = engine_orchestrator
        self._instances: dict[str, Instance] = {}  # serial → Instance
        self._active_serial: str | None = None
        self._stun: StunServer | None = None
        self._stun_ip: str | None = None          # IP the STUN server is bound to
        self._lock = threading.Lock()
        self._engine_refresh_lock = threading.Lock()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog, daemon=True
        )
        self._watchdog_thread.start()

    def set_webrtc_manager(self, webrtc: "WebrtcManager") -> None:
        """Late-bind the WebrtcManager once an event loop exists (called
        from app.py's startup handler) -- WebrtcManager itself requires a
        running loop, which main.py's construction point predates.
        """
        self._webrtc = webrtc

    # ── Discovery ────────────────────────────────────────────────────────────

    def refresh(self):
        """Discover connected LDPlayer instances and start scrcpy for new ones."""
        if self._engine_orchestrator is None:
            self._refresh_once()
            return
        # Serialize the complete discovery transition so a stale removal cannot
        # overtake a concurrent rediscovery of the same serial. This is separate
        # from _lock: engine cleanup and startup remain blocking work
        # performed without holding the instance registry lock.
        with self._engine_refresh_lock:
            self._refresh_once()

    def _refresh_once(self):
        vms = adb_manager.list_vms()
        current_serials = {vm["id"][4:] for vm in vms}

        with self._lock:
            gone = set(self._instances) - current_serials
            gone_instances = [self._instances.pop(serial) for serial in gone]
            if self._active_serial in gone:
                self._active_serial = None
            new_vms = [v for v in vms if v["id"][4:] not in self._instances]
            # Build mediamtx paths from current tracked instances + new ones
            existing_names = [inst.name for inst in self._instances.values()]
            new_names = [instance_name(v["id"][4:]) for v in new_vms]
            all_names = existing_names + new_names

        if self._engine_orchestrator is not None:
            for gone_instance in gone_instances:
                _log(f"[instance] device gone: {gone_instance.serial}")
                self._engine_orchestrator.remove_instance(gone_instance.serial)
            for vm in new_vms:
                serial = vm["id"][4:]
                width, height = adb_manager.get_screen_size(serial)
                inst = Instance(vm, None, width, height)
                self._engine_orchestrator.add_instance(
                    serial, inst.name, vm["ldplayer_index"], inst.tier,
                )
                with self._lock:
                    if serial in current_serials:
                        self._instances[serial] = inst
                    else:
                        self._engine_orchestrator.remove_instance(serial)
            return

        gone_names = [inst.name for inst in gone_instances]
        for gone_instance in gone_instances:
            _log(f"[instance] device gone: {gone_instance.serial}")
            gone_instance.session.stop()

        if not new_vms and not gone:
            return

        from server.tailscale import get_best_ip
        _ip = get_best_ip()
        self._ensure_stun(_ip)

        if self._webrtc is None and self._mediamtx is not None:
            # mediamtx backend: one always-live RTSP/WHEP path per instance.
            # The self._mediamtx is not None guard is defensive, not the fix
            # for the real bug: app.py's _startup() ordering is what
            # guarantees set_webrtc_manager() runs before refresh() can ever
            # execute under WEBRTC_BACKEND=="aiortc" (main.py constructs this
            # manager with mediamtx=None in that mode). This just means a
            # future ordering mistake degrades to a skipped mediamtx-path
            # setup instead of crashing this call's background thread with
            # AttributeError: 'NoneType' object has no attribute 'running'.
            if not self._mediamtx.running:
                _log(f"[mediamtx] booting mediamtx, advertising IP for ICE: {_ip}")
                self._mediamtx.start(all_names, tailscale_ip=_ip)
            else:
                # Patch the running instance's path list via its config API
                # instead of restarting the whole process — a restart tears
                # down every other instance's live WHEP stream, not just the
                # one that changed.
                for name in gone_names:
                    self._mediamtx.remove_path(name)
                for name in new_names:
                    self._mediamtx.add_path(name)
        # aiortc backend: no per-path setup needed -- WebrtcManager tracks
        # viewers per instance name lazily, on first WHEP POST.

        # Start scrcpy sessions for new devices
        for vm in new_vms:
            serial = vm["id"][4:]
            w, h = adb_manager.get_screen_size(serial)
            name = instance_name(serial)
            rtsp_url = self._mediamtx.rtsp_url(name) if self._mediamtx is not None else ""
            idx = vm["ldplayer_index"]
            session = ScrcpySession(serial, idx, rtsp_url, w, h)
            ok = session.start()
            _log(f"[instance] started serial={serial} ok={ok}")
            inst = Instance(vm, session, w, h)
            with self._lock:
                self._instances[serial] = inst

    def _ensure_stun(self, ip: str | None):
        """Start (or rebind) the STUN server on the current Tailscale IP.

        The STUN server must be bound to the Tailscale interface so the srflx
        candidate it reports to the browser is the browser's *Tailscale* IP —
        the only address mediamtx can reach. Rebind if the IP changed.
        """
        if not ip:
            return
        if self._stun is not None and self._stun_ip == ip:
            return
        if self._stun is not None:
            self._stun.stop()
        from config import STUN_PORT
        self._stun = StunServer(ip, STUN_PORT)
        self._stun.start()
        self._stun_ip = ip

    # ── Active session ───────────────────────────────────────────────────────

    def select(self, serial: str) -> bool:
        """Mark an instance active for input routing.

        The browser WHEPs directly to this instance's own mediamtx path, so
        there is no mux to repoint — select() only records which instance the
        input path targets. It still ensures the target's scrcpy is publishing
        (starts a dead session) so the WHEP the client is about to make lands on
        a live path.
        """
        with self._lock:
            inst = self._instances.get(serial)
            if inst is None:
                return False
        if not inst.session.alive:
            _log(f"[instance] select {serial}: session not alive — starting")
            inst.session.start()
        if not inst.session.alive:
            _log(f"[instance] select {serial}: session still not alive")
            return False
        # Copy-mux has no ffmpeg GOP — keyframes come from the ~2s IDR heartbeat.
        # On a switch the browser WHEPs to this path and can't render until it
        # sees an IDR, so force one now instead of making it wait for the next
        # heartbeat tick (up to ~2s of black screen). Best-effort: control socket
        # may not be connected yet on a just-started session; the heartbeat still
        # covers that case.
        try:
            inst.session.control.request_idr()
        except Exception:
            pass
        with self._lock:
            self._active_serial = serial
        return True

    def get(self, serial: str) -> Instance | None:
        """Return the tracked Instance for a serial, or None."""
        with self._lock:
            return self._instances.get(serial)

    def engine_enabled(self) -> bool:
        return self._engine_orchestrator is not None

    def select_engine(self, serial: str,
                      advertised_host: str) -> EngineSelection | None:
        if self._engine_orchestrator is None:
            return None
        selection = self._engine_orchestrator.select(serial, advertised_host)
        if selection is not None:
            with self._lock:
                if serial in self._instances:
                    self._active_serial = serial
        return selection

    def get_by_name(self, name: str) -> Instance | None:
        """Look up a tracked Instance by its mediamtx path name (e.g.
        'instance0'), not its ADB serial. mediamtx's runOnDemand/
        runOnUnDemand hooks only know the path name (`$MTX_PATH`), so this
        is the entry point the publish_hook.py script's HTTP calls resolve
        through.
        """
        with self._lock:
            for inst in self._instances.values():
                if inst.name == name:
                    return inst
            return None

    def start_video(self, name: str) -> bool:
        """Start the on-demand video half for the instance at path `name`.
        Routes to the aiortc backend if a WebrtcManager is configured,
        otherwise to the mediamtx/ffmpeg backend -- the two are mutually
        exclusive per InstanceManager instance (see config.WEBRTC_BACKEND).
        """
        inst = self.get_by_name(name)
        if inst is None:
            return False
        if self._webrtc is not None:
            return inst.session.start_video_aiortc(
                on_frame=lambda nalu, n=name: self._webrtc.push_nalu_threadsafe(n, nalu)
            )
        return inst.session.start_video()

    def stop_video(self, name: str) -> None:
        """Stop the on-demand video half for the instance at path `name`."""
        inst = self.get_by_name(name)
        if inst is None:
            return
        if self._webrtc is not None:
            inst.session.stop_video_aiortc()
        else:
            inst.session.stop_video()

    @property
    def active(self) -> Instance | None:
        with self._lock:
            if self._active_serial and self._active_serial in self._instances:
                return self._instances[self._active_serial]
            return None

    def set_tier(self, serial: str, tier: str) -> bool:
        """Update quality tier for a specific instance.

        Routes to the instance's session, which validates the tier and restarts
        if running. Updates Instance.tier on success.

        Args:
            serial: Device serial (e.g., "emulator-5554").
            tier: Quality tier name (must be in QUALITY_TIERS).

        Returns:
            True if tier was accepted/set, False if serial unknown or tier invalid.
        """
        with self._lock:
            inst = self._instances.get(serial)
        if inst is None:
            return False
        if self._engine_orchestrator is not None:
            ok = self._engine_orchestrator.set_tier(serial, tier)
        else:
            ok = inst.session.set_tier(tier)
        if ok:
            inst.tier = tier
        return ok

    def request_keyframe(self, serial: str) -> None:
        if self._engine_orchestrator is not None:
            self._engine_orchestrator.request_keyframe(serial)
            return
        inst = self.get(serial)
        if inst is not None:
            try:
                inst.session.control.request_idr()
            except Exception:
                pass

    # ── REST data ────────────────────────────────────────────────────────────

    def list_instances(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "id": inst.id,
                    "serial": inst.serial,
                    "title": inst.title,
                    "name": inst.name,
                    "w": inst.w,
                    "h": inst.h,
                    "active": inst.serial == self._active_serial,
                }
                for inst in self._instances.values()
            ]

    # ── Watchdog ─────────────────────────────────────────────────────────────

    def _watchdog(self):
        while True:
            time.sleep(10)
            try:
                if self._engine_orchestrator is not None:
                    self.refresh()
                    self._engine_orchestrator.check_all()
                    continue
                with self._lock:
                    dead = [
                        inst for inst in self._instances.values()
                        if not inst.session.alive
                    ]
                # Restart each dead session in its own thread to avoid serializing
                # the 1.5s scrcpy startup sleep across all instances
                threads = []
                for inst in dead:
                    t = threading.Thread(
                        target=self._restart_session, args=(inst,), daemon=True
                    )
                    t.start()
                    threads.append(t)
                for t in threads:
                    t.join()
            except Exception:
                _log(f"[instance] watchdog error: {traceback.format_exc()[:300]}")

    def _restart_session(self, inst: Instance):
        # restart_if_dead re-checks aliveness under the session's restart lock,
        # so a tier-change restart already in flight is not double-started (the
        # race that corrupted the scrcpy handshake with a truncated read).
        ok = inst.session.restart_if_dead()
        _log(f"[instance] watchdog restart serial={inst.serial} ok={ok}")
        # If device disconnected while we were restarting, stop the orphaned session
        with self._lock:
            if inst.serial not in self._instances:
                _log(f"[instance] watchdog: {inst.serial} gone during restart — stopping orphan")
                inst.session.stop()

    def stop_all(self):
        if self._engine_orchestrator is not None:
            with self._lock:
                self._instances.clear()
                self._active_serial = None
            self._engine_orchestrator.stop_all()
        else:
            with self._lock:
                for inst in self._instances.values():
                    inst.session.stop()
                self._instances.clear()
                self._active_serial = None
        if self._mediamtx is not None:
            self._mediamtx.stop()
