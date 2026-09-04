"""Discover LDPlayer devices and keep one mandatory engine runtime per device."""

import threading
import time
import traceback
from typing import TYPE_CHECKING

from config import DEFAULT_TIER
from server import adb_manager
from server.engine_runtime import EngineSelection
from server.stun_server import StunServer

if TYPE_CHECKING:
    from server.engine_orchestrator import EngineOrchestrator


def _log(msg: str):
    import os

    for path in [r"C:\ProgramData\WindowControl", r"C:\Windows\Temp"]:
        try:
            os.makedirs(path, exist_ok=True)
            with open(os.path.join(path, "service_crash.log"), "a") as log_file:
                log_file.write(msg + "\n")
            return
        except Exception:
            continue


def instance_name(serial: str) -> str:
    """Return a stable engine instance name for an ADB serial."""
    import re

    match = re.match(r"emulator-(\d+)", serial)
    if match:
        port = int(match.group(1))
        index = (port - 5554) // 2
        return f"instance{index}"
    return f"instance_{serial.replace(':', '_')}"


class Instance:
    def __init__(self, vm: dict, width: int, height: int):
        self.id = vm["id"]
        self.serial = vm["id"][4:]
        self.title = vm["title"]
        self.ldplayer_index = vm["ldplayer_index"]
        self.name = instance_name(self.serial)
        self.w = width
        self.h = height
        self.tier = DEFAULT_TIER


class InstanceManager:
    def __init__(self, engine_orchestrator: "EngineOrchestrator"):
        self._engine_orchestrator = engine_orchestrator
        self._instances: dict[str, Instance] = {}
        self._active_serial: str | None = None
        self._stun: StunServer | None = None
        self._stun_ip: str | None = None
        self._lock = threading.Lock()
        self._engine_refresh_lock = threading.Lock()
        self._watchdog_thread = threading.Thread(
            target=self._watchdog, daemon=True
        )
        self._watchdog_thread.start()

    def refresh(self):
        """Reconcile discovered devices with mandatory engine runtimes."""
        with self._engine_refresh_lock:
            self._refresh_once()

    def _refresh_once(self):
        from server.tailscale import get_best_ip

        vms = adb_manager.list_vms()
        current_serials = {vm["id"][4:] for vm in vms}

        # The browser needs an embedded STUN binding on the current best local
        # interface before any newly published engine endpoint is selected.
        self._ensure_stun(get_best_ip())

        with self._lock:
            gone = set(self._instances) - current_serials
            gone_instances = [self._instances.pop(serial) for serial in gone]
            if self._active_serial in gone:
                self._active_serial = None
            new_vms = [vm for vm in vms if vm["id"][4:] not in self._instances]

        for instance in gone_instances:
            _log(f"[instance] device gone: {instance.serial}")
            self._engine_orchestrator.remove_instance(instance.serial)

        for vm in new_vms:
            serial = vm["id"][4:]
            width, height = adb_manager.get_screen_size(serial)
            instance = Instance(vm, width, height)
            self._engine_orchestrator.add_instance(
                serial,
                instance.name,
                vm["ldplayer_index"],
                instance.tier,
            )
            with self._lock:
                if serial in current_serials:
                    self._instances[serial] = instance
                    continue
            self._engine_orchestrator.remove_instance(serial)

    def _ensure_stun(self, ip: str | None):
        """Start or rebind embedded STUN when the advertised IP changes."""
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

    def select(
        self, serial: str, advertised_host: str, user_id: str | None = None
    ) -> EngineSelection | None:
        selection = self._engine_orchestrator.select(
            serial, advertised_host, user_id=user_id
        )
        if selection is not None:
            with self._lock:
                if serial in self._instances:
                    self._active_serial = serial
        return selection

    def get(self, serial: str) -> Instance | None:
        with self._lock:
            return self._instances.get(serial)

    @property
    def active(self) -> Instance | None:
        with self._lock:
            if self._active_serial and self._active_serial in self._instances:
                return self._instances[self._active_serial]
            return None

    def set_tier(self, serial: str, tier: str) -> bool:
        with self._lock:
            instance = self._instances.get(serial)
        if instance is None:
            return False
        accepted = self._engine_orchestrator.set_tier(serial, tier)
        if accepted:
            instance.tier = tier
        return accepted

    def request_keyframe(self, serial: str) -> None:
        self._engine_orchestrator.request_keyframe(serial)

    def list_instances(self) -> list[dict]:
        with self._lock:
            return [
                {
                    "id": instance.id,
                    "serial": instance.serial,
                    "title": instance.title,
                    "name": instance.name,
                    "w": instance.w,
                    "h": instance.h,
                    "active": instance.serial == self._active_serial,
                }
                for instance in self._instances.values()
            ]

    def _watchdog(self):
        while True:
            time.sleep(10)
            try:
                self.refresh()
                self._engine_orchestrator.check_all()
            except Exception:
                _log(f"[instance] watchdog error: {traceback.format_exc()[:300]}")

    def stop_all(self):
        with self._engine_refresh_lock:
            with self._lock:
                self._instances.clear()
                self._active_serial = None
            self._engine_orchestrator.stop_all()
            if self._stun is not None:
                self._stun.stop()
                self._stun = None
                self._stun_ip = None
