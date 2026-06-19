"""
InstanceManager: auto-discovers LDPlayer ADB devices, manages one ScrcpySession
per instance, and exposes the active session for input routing.

Auto-restart: a watchdog thread checks all sessions every 10s and restarts
any that have crashed.
"""

import threading
import time
import traceback

from server import adb_manager
from server.scrcpy_session import ScrcpySession
from server.mediamtx_manager import MediamtxManager


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
    def __init__(self, vm: dict, session: ScrcpySession, w: int, h: int):
        self.id = vm["id"]               # "adb:SERIAL"
        self.serial = vm["id"][4:]       # "SERIAL"
        self.title = vm["title"]
        self.ldplayer_index = vm["ldplayer_index"]
        self.name = instance_name(self.serial)
        self.w = w
        self.h = h
        self.session = session


class InstanceManager:
    def __init__(self, mediamtx: MediamtxManager):
        self._mediamtx = mediamtx
        self._instances: dict[str, Instance] = {}  # serial → Instance
        self._active_serial: str | None = None
        self._lock = threading.Lock()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog, daemon=True
        )
        self._watchdog_thread.start()

    # ── Discovery ────────────────────────────────────────────────────────────

    def refresh(self):
        """Discover connected LDPlayer instances and start scrcpy for new ones."""
        vms = adb_manager.list_vms()
        current_serials = {vm["id"][4:] for vm in vms}

        with self._lock:
            # Stop sessions for devices that disconnected
            gone = set(self._instances) - current_serials
            for serial in gone:
                _log(f"[instance] device gone: {serial}")
                self._instances[serial].session.stop()
                del self._instances[serial]
            if self._active_serial in gone:
                self._active_serial = None
            new_vms = [v for v in vms if v["id"][4:] not in self._instances]
            # Build mediamtx paths from current tracked instances + new ones
            existing_names = [inst.name for inst in self._instances.values()]
            new_names = [instance_name(v["id"][4:]) for v in new_vms]
            all_names = existing_names + new_names

        if not new_vms and not gone:
            return

        # Restart mediamtx with updated path list, advertising Tailscale IP for fast ICE
        from server.tailscale import get_best_ip
        self._mediamtx.start(all_names, tailscale_ip=get_best_ip())

        # Start scrcpy sessions for new devices
        for vm in new_vms:
            serial = vm["id"][4:]
            w, h = adb_manager.get_screen_size(serial)
            name = instance_name(serial)
            rtsp_url = self._mediamtx.rtsp_url(name)
            idx = vm["ldplayer_index"]
            session = ScrcpySession(serial, idx, rtsp_url, w, h)
            ok = session.start()
            _log(f"[instance] started serial={serial} ok={ok}")
            inst = Instance(vm, session, w, h)
            with self._lock:
                self._instances[serial] = inst

    # ── Active session ───────────────────────────────────────────────────────

    def select(self, serial: str) -> bool:
        with self._lock:
            if serial not in self._instances:
                return False
            self._active_serial = serial
            return True

    @property
    def active(self) -> Instance | None:
        with self._lock:
            if self._active_serial and self._active_serial in self._instances:
                return self._instances[self._active_serial]
            return None

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
        _log(f"[instance] watchdog: restarting dead session serial={inst.serial}")
        inst.session.stop()
        ok = inst.session.start()
        _log(f"[instance] watchdog restart serial={inst.serial} ok={ok}")
        # If device disconnected while we were restarting, stop the orphaned session
        with self._lock:
            if inst.serial not in self._instances:
                _log(f"[instance] watchdog: {inst.serial} gone during restart — stopping orphan")
                inst.session.stop()

    def stop_all(self):
        with self._lock:
            for inst in self._instances.values():
                inst.session.stop()
            self._instances.clear()
            self._active_serial = None
        self._mediamtx.stop()
