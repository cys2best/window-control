"""Serialized lifecycle manager for one instance's engine.exe orchestration.

One `EngineRuntime` owns exactly one Android instance's streaming pair:
a scrcpy-server process (via `ScrcpyServerLauncher`) and an engine.exe process
(via `EngineInstance`). Every public method runs under a single re-entrant lock,
so tier changes, watchdog ticks, selections, and teardown can never interleave.

Three different failures all funnel through one generation-checked reconnect:
  - a quality-tier change (operator-initiated),
  - a stalled/disconnected video source (watchdog-initiated),
  - a dead engine process (full respawn, not a reconnect).

The engine is the source of truth for the generation counter. This module never
invents a generation: it proposes `base + 1`, and if the engine rejects that as
stale it retries exactly once from the generation the engine reported. A second
rejection, or any transport/protocol failure, leaves the runtime degraded for
the next watchdog tick rather than fabricating a recovery.

Credentials are minted per call and never cached: `select()` issues a fresh WHEP
capability token and a fresh viewer signaling token every time, because both are
short-lived. Only non-expiring endpoint metadata (ports, generation, dimensions)
is retained between calls.
"""

import threading
import time
from dataclasses import dataclass
from typing import Callable, Literal, Optional

from config import QUALITY_TIERS

from server.engine_admin import (
    EngineAdminClient,
    EngineAdminProtocolError,
    EngineAdminUnavailable,
    EngineHealth,
    ReconnectRejected,
)
from server.engine_auth import EngineTokenIssuer
from server.engine_process import EngineInstance, EngineReadyRecord
from server.scrcpy_server import ScrcpyServerLauncher

CheckResult = Literal["healthy", "grace", "recovered", "degraded", "respawned"]

_HEALTHY_STATE = "connected"
_RECOVERABLE_STATES = ("stalled", "disconnected")


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


@dataclass(frozen=True)
class EngineRuntimeConfig:
    """Immutable process-wide engine configuration.

    `signaling_url` empty disables the public signaling path entirely: the
    engine still runs, but `select()` reports no signaling endpoint and no
    viewer token.
    """
    exe_path: str
    whep_secret: str
    signaling_url: str
    signaling_secret: str
    local_ice_servers: tuple[str, ...]
    public_ice_servers: tuple[str, ...]


@dataclass(frozen=True)
class EngineSelection:
    """What a client needs to start playing. Deliberately has no admin port —
    the admin listener is loopback-only and never client-facing."""
    whep_url: str
    whep_token: str
    signaling_url: str | None
    signaling_token: str | None
    generation: int
    width: int
    height: int


@dataclass
class _Endpoint:
    """Non-expiring endpoint metadata from the engine's ready record / health."""
    whep_port: int
    admin_port: int
    generation: int
    width: int
    height: int


def _format_host(host: str) -> str:
    """Bracket bare IPv6 literals so they can carry a port."""
    if host.startswith("[") and host.endswith("]"):
        return host
    if ":" in host:
        return f"[{host}]"
    return host


class EngineRuntime:
    def __init__(self, serial: str, instance_name: str, instance_index: int,
                 initial_tier: str, config: EngineRuntimeConfig,
                 launcher: ScrcpyServerLauncher,
                 admin: EngineAdminClient,
                 token_issuer: EngineTokenIssuer,
                 engine_factory: Callable[..., EngineInstance] = EngineInstance,
                 clock: Callable[[], float] = time.monotonic,
                 stall_grace_seconds: float = 3.0,
                 log: Callable[[str], None] = _log):
        self.serial = serial
        self.instance_name = instance_name
        self.instance_index = instance_index
        self.config = config

        self._launcher = launcher
        self._admin = admin
        self._token_issuer = token_issuer
        self._engine_factory = engine_factory
        self._clock = clock
        self._stall_grace_seconds = stall_grace_seconds
        self._log = log

        # Everything below is guarded by _lock. It is re-entrant so that the
        # public entry points can share private _locked helpers freely.
        self._lock = threading.RLock()
        self._tier = initial_tier
        self._engine: Optional[EngineInstance] = None
        self._endpoint: Optional[_Endpoint] = None
        self._scrcpy_port: Optional[int] = None
        self._stalled_since: Optional[float] = None
        self._stopped = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def start(self) -> None:
        """Launch scrcpy generation 0, then the engine, and record its ready
        endpoint. Idempotent while an engine is already live."""
        with self._lock:
            if self._stopped:
                return
            if self._endpoint is not None and self._engine is not None:
                return
            self._fresh_start_locked()

    def select(self, advertised_host: str) -> EngineSelection | None:
        """Mint a fresh, short-lived credential set for one client.

        Returns None when no engine endpoint is currently published (never
        started, start failed, respawn failed, or stopped).
        """
        with self._lock:
            if self._stopped or self._endpoint is None:
                return None

            endpoint = self._endpoint
            host = _format_host(advertised_host)

            signaling_url: str | None = None
            signaling_token: str | None = None
            if self.config.signaling_url:
                signaling_url = self.config.signaling_url
                signaling_token = self._token_issuer.signaling(
                    self.instance_name, "viewer"
                )

            return EngineSelection(
                whep_url=f"http://{host}:{endpoint.whep_port}/whep",
                whep_token=self._token_issuer.whep(self.instance_name),
                signaling_url=signaling_url,
                signaling_token=signaling_token,
                generation=endpoint.generation,
                width=endpoint.width,
                height=endpoint.height,
            )

    def set_tier(self, tier: str) -> None:
        """Relaunch scrcpy at a new quality tier and hand the engine the new
        source through a generation-checked reconnect."""
        if tier not in QUALITY_TIERS:
            raise ValueError(f"unknown quality tier: {tier}")

        with self._lock:
            if self._stopped or self._endpoint is None:
                return
            if tier == self._tier:
                return

            # The engine's own counter is authoritative (it rejects any
            # requested generation <= its current one), so ask it before
            # proposing a successor rather than trusting stored state. If the
            # poll fails we still proceed from the last known generation: a
            # stale base is exactly what _reconnect_locked's retry-once path
            # corrects, and a transient health blip should not block a
            # user-requested quality change.
            base_generation = self._endpoint.generation
            try:
                health = self._admin.health(self._endpoint.admin_port)
            except (EngineAdminUnavailable, EngineAdminProtocolError) as e:
                self._log(
                    f"[engine] pre-tier health failed for {self.instance_name}: {e}"
                )
            else:
                self._apply_health_locked(health)
                base_generation = health.generation

            self._reconnect_locked(tier, base_generation)
            self._tier = tier

    def request_keyframe(self) -> None:
        """Ask the engine for an IDR. Best-effort: a failure here is not worth
        escalating, the watchdog will notice anything real."""
        with self._lock:
            if self._stopped or self._endpoint is None:
                return
            try:
                self._admin.keyframe(self._endpoint.admin_port)
            except (EngineAdminUnavailable, EngineAdminProtocolError) as e:
                self._log(f"[engine] keyframe failed for {self.instance_name}: {e}")

    def check_once(self) -> CheckResult:
        """One watchdog tick. See the module docstring for the decision tree."""
        with self._lock:
            if self._stopped:
                return "degraded"

            # No engine at all (never started, or a previous start/respawn
            # failed): retry the whole fresh-start sequence.
            if self._engine is None or self._endpoint is None:
                try:
                    self._fresh_start_locked()
                except Exception as e:
                    self._log(
                        f"[engine] retry start failed for {self.instance_name}: {e}"
                    )
                    return "degraded"
                return "respawned"

            # Process liveness is checked BEFORE any admin HTTP call: a dead
            # process needs a respawn, and its admin port may still be
            # accepting connections briefly or belong to something else.
            if not self._engine.is_running():
                try:
                    self._respawn_locked()
                except Exception as e:
                    self._log(
                        f"[engine] respawn failed for {self.instance_name}: {e}"
                    )
                    return "degraded"
                return "respawned"

            try:
                health = self._admin.health(self._endpoint.admin_port)
            except (EngineAdminUnavailable, EngineAdminProtocolError) as e:
                # A live process with an unreachable or misbehaving admin
                # listener is NOT the same failure as a dead process. Never
                # respawn on this path.
                self._log(f"[engine] health failed for {self.instance_name}: {e}")
                return "degraded"

            if health.state == _HEALTHY_STATE:
                self._stalled_since = None
                self._apply_health_locked(health)
                return "healthy"

            if health.state not in _RECOVERABLE_STATES:
                self._log(
                    f"[engine] unknown health state for {self.instance_name}: "
                    f"{health.state!r}"
                )
                return "degraded"

            now = self._clock()
            if self._stalled_since is None:
                self._stalled_since = now
            if now - self._stalled_since < self._stall_grace_seconds:
                return "grace"

            # The generation just reported by the engine is authoritative — the
            # engine rejects anything <= its own counter — so the successor is
            # computed from the health response, never from a Python-side
            # launch counter that could have drifted.
            try:
                self._reconnect_locked(self._tier, health.generation)
            except Exception as e:
                # Stays degraded, stall timer intact, so the next tick retries.
                self._log(
                    f"[engine] stall recovery failed for {self.instance_name}: {e}"
                )
                raise

            self._stalled_since = None
            return "recovered"

    def stop(self) -> None:
        """Mark stopped under the same lock, then tear down engine + scrcpy.

        Taking the lock means an in-flight recovery finishes first rather than
        being torn down mid-reconnect.
        """
        with self._lock:
            if self._stopped:
                return
            self._stopped = True

            engine, self._engine = self._engine, None
            self._endpoint = None
            self._scrcpy_port = None
            self._stalled_since = None

            if engine is not None:
                try:
                    engine.stop()
                except Exception as e:
                    self._log(f"[engine] stop failed for {self.instance_name}: {e}")
            try:
                self._launcher.stop()
            except Exception as e:
                self._log(f"[engine] launcher stop failed for {self.instance_name}: {e}")

    # ------------------------------------------------------------------
    # Private helpers (all called with _lock held)
    # ------------------------------------------------------------------

    def _build_env_locked(self) -> dict[str, str]:
        return {
            "ENGINE_WHEP_CAPABILITY_SECRET": self.config.whep_secret,
            "ENGINE_LOCAL_ICE_SERVERS": ",".join(self.config.local_ice_servers),
            "ENGINE_SIGNALING_URL": self.config.signaling_url,
            "ENGINE_SIGNALING_TOKEN": self._token_issuer.signaling(
                self.instance_name, "engine"
            ),
            "ENGINE_PUBLIC_ICE_SERVERS": ",".join(self.config.public_ice_servers),
        }

    def _fresh_start_locked(self) -> None:
        """Launch scrcpy at generation 0 and spawn a new engine for it.

        On any failure the launcher is rolled back and no endpoint metadata is
        published, so `select()` keeps returning None instead of pointing
        clients at a port nothing is listening on.
        """
        launch = self._launcher.launch(self._tier, 0)
        try:
            engine = self._engine_factory(
                instance_name=self.instance_name,
                exe_path=self.config.exe_path,
                scrcpy_port=launch.port,
                env_overrides=self._build_env_locked(),
            )
            record: EngineReadyRecord = engine.start()
        except Exception:
            try:
                self._launcher.stop()
            except Exception:
                pass
            raise

        self._engine = engine
        self._scrcpy_port = launch.port
        self._stalled_since = None
        self._endpoint = _Endpoint(
            whep_port=record.whep_port,
            admin_port=record.admin_port,
            generation=record.generation,
            width=record.width,
            height=record.height,
        )

    def _respawn_locked(self) -> None:
        """Replace a dead engine process wholesale.

        The old handle is stopped first, then a brand-new scrcpy generation 0
        and a brand-new engine. Endpoint metadata is dropped up front (the old
        ports are meaningless) and only republished from the NEW ready record —
        the engine picks its ports dynamically, so they routinely change.
        """
        engine, self._engine = self._engine, None
        self._endpoint = None
        self._scrcpy_port = None

        if engine is not None:
            try:
                engine.stop()
            except Exception as e:
                self._log(f"[engine] old handle stop failed for {self.instance_name}: {e}")
        try:
            self._launcher.stop()
        except Exception as e:
            self._log(f"[engine] old forward stop failed for {self.instance_name}: {e}")

        self._fresh_start_locked()

    def _reconnect_locked(self, tier: str, base_generation: int) -> None:
        """Point the live engine at a freshly launched scrcpy source.

        Launches scrcpy EXACTLY ONCE, at `base_generation + 1`. If the engine
        rejects that generation as stale it reports its own current generation,
        which is authoritative; we retry the very same launched port at
        `current + 1`. A second rejection, or any other admin failure,
        propagates: the caller stays degraded and the next watchdog tick tries
        again. This never returns without an accepted reconnect response.
        """
        assert self._endpoint is not None
        admin_port = self._endpoint.admin_port

        launch = self._launcher.launch(tier, base_generation + 1)
        self._scrcpy_port = launch.port

        try:
            accepted = self._admin.reconnect(
                admin_port, launch.port, base_generation + 1
            )
        except ReconnectRejected as rejection:
            # The engine's counter moved on under us (a concurrent reconnect,
            # or our base was stale). Retry once from its own truth, reusing
            # the same launched source rather than churning scrcpy again.
            accepted = self._admin.reconnect(
                admin_port, launch.port, rejection.current_generation + 1
            )

        self._endpoint.generation = accepted

        # Dimensions come from the engine's post-reconnect health, never
        # synthesized from the requested tier: the device decides the real
        # output size, and the tier is only a ceiling hint.
        try:
            health = self._admin.health(admin_port)
        except (EngineAdminUnavailable, EngineAdminProtocolError) as e:
            self._log(
                f"[engine] post-reconnect health failed for {self.instance_name}: {e}"
            )
            return
        self._apply_health_locked(health)

    def _apply_health_locked(self, health: EngineHealth) -> None:
        if self._endpoint is None:
            return
        self._endpoint.generation = health.generation
        self._endpoint.width = health.width
        self._endpoint.height = health.height
