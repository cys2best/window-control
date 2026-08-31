"""Discovery-level registry for one EngineRuntime per emulator instance."""

import threading
from typing import Callable

from server.engine_admin import EngineAdminClient
from server.engine_auth import EngineTokenIssuer
from server.engine_runtime import (
    EngineRuntime,
    EngineRuntimeConfig,
    EngineSelection,
    _log,
)
from server.scrcpy_server import ScrcpyServerLauncher


class EngineOrchestrator:
    def __init__(self, config: EngineRuntimeConfig,
                 runtime_factory: Callable[..., EngineRuntime] = EngineRuntime,
                 log: Callable[[str], None] = _log):
        self.config = config
        self._runtime_factory = runtime_factory
        self._log = log
        self._lock = threading.Lock()
        self._runtimes: dict[str, EngineRuntime] = {}
        self._admin = EngineAdminClient()
        self._token_issuer = EngineTokenIssuer(
            config.whep_secret, config.signaling_secret
        )

    def add_instance(self, serial: str, instance_name: str,
                     instance_index: int, tier: str) -> None:
        with self._lock:
            if serial in self._runtimes:
                return

        launcher = ScrcpyServerLauncher(serial, instance_index)
        runtime = self._runtime_factory(
            serial,
            instance_name,
            instance_index,
            tier,
            self.config,
            launcher,
            self._admin,
            self._token_issuer,
        )

        with self._lock:
            existing = self._runtimes.get(serial)
            if existing is None:
                self._runtimes[serial] = runtime

        if existing is not None:
            runtime.stop()
            return

        try:
            runtime.start()
        except Exception as error:
            self._log(f"[engine] initial start failed for {instance_name}: {error}")

    def remove_instance(self, serial: str) -> None:
        with self._lock:
            runtime = self._runtimes.pop(serial, None)
        if runtime is not None:
            runtime.stop()

    def select(self, serial: str, advertised_host: str) -> EngineSelection | None:
        with self._lock:
            runtime = self._runtimes.get(serial)
        if runtime is None:
            return None
        return runtime.select(advertised_host)

    def set_tier(self, serial: str, tier: str) -> bool:
        with self._lock:
            runtime = self._runtimes.get(serial)
        if runtime is None:
            return False
        runtime.set_tier(tier)
        return True

    def request_keyframe(self, serial: str) -> None:
        with self._lock:
            runtime = self._runtimes.get(serial)
        if runtime is not None:
            runtime.request_keyframe()

    def check_all(self) -> dict[str, str]:
        with self._lock:
            runtimes = tuple(self._runtimes.items())

        results: dict[str, str] = {}
        for serial, runtime in runtimes:
            try:
                results[serial] = runtime.check_once()
            except Exception as error:
                self._log(f"[engine] check failed for {serial}: {error}")
                results[serial] = "degraded"
        return results

    def stop_all(self) -> None:
        with self._lock:
            runtimes = tuple(self._runtimes.values())
            self._runtimes.clear()

        for runtime in runtimes:
            try:
                runtime.stop()
            except Exception as error:
                self._log(f"[engine] stop failed: {error}")
