"""One installation-wide remote media owner, confined to the host event loop.

State decisions never await. Engine I/O and blocking runtime locks are outside
those decisions; canceled work remains supervised until its late peer is closed.
"""

import asyncio
from dataclasses import dataclass, field
import time
import uuid
from typing import Callable, TYPE_CHECKING

from remote_protocol import Command, ErrorCode, MediaBundles, RoutingContext
from server.engine_remote import EngineRemoteClient, RemoteEngineEndpoint
from server.instance_manager import MutationGuard
from server.pairing import PairingStore, PairedDevice
from server.remote_pairing import RemotePairingError

if TYPE_CHECKING:
    from server.remote_client import RemoteHostClient
    from server.remote_dispatch import InstanceActions


@dataclass
class RemoteSelection:
    session_id: str
    generation: int
    device_id: str
    serial: str
    context: RoutingContext = field(repr=False)
    mutation_guard: MutationGuard | None = field(default=None, repr=False)
    request_ids: set[str] = field(default_factory=set)
    endpoint: RemoteEngineEndpoint | None = None
    peer_id: str | None = None
    canceled: bool = False
    negotiating: bool = False
    task: asyncio.Task | None = None
    host_ice: list = field(default_factory=list, repr=False)
    selected_at: float = 0
    admission_attempted: bool = False
    retirement: asyncio.Task | None = None


class RemoteSessionCoordinator:
    def __init__(self, installation_id: str, actions: "InstanceActions", pairing: PairingStore,
                 endpoint: Callable[[str], RemoteEngineEndpoint | None], engine: EngineRemoteClient,
                 *, authority: "RemoteHostClient", clock=time.monotonic):
        self.installation_id = installation_id
        self.actions = actions
        self.pairing = pairing
        self.endpoint = endpoint
        self.engine = engine
        self.authority = authority
        self.clock = clock
        self.current = None
        self._generation = 0
        self._tasks = set()
        self._cleanup = {}
        self._disconnected_at = None

    def _authorized(self, device, context):
        current = self.pairing.device_for_token(context.token)
        if current is None or device is None or current.id != device.id:
            raise RemotePairingError(ErrorCode.NOT_PAIRED, "Device is not paired")
        if context.installation_id != self.installation_id:
            raise RemotePairingError(ErrorCode.NOT_PAIRED, "Wrong installation")

    def _valid(self, session):
        device = self.pairing.device_for_token(session.context.token)
        return (self.current is session and not session.canceled
                and device is not None and device.id == session.device_id)

    def _track(self, coroutine):
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        def settled(done):
            self._tasks.discard(done)
            if not done.cancelled():
                done.exception()
        task.add_done_callback(settled)
        return task

    async def _fresh_endpoint(self, session):
        retained = session.endpoint
        if retained is None:
            raise RemotePairingError(ErrorCode.UNAVAILABLE, "Remote peer cleanup is pending")
        fresh = await asyncio.to_thread(self.endpoint, session.serial)
        if fresh is not None and fresh.owner is retained.owner:
            return fresh
        if not retained.owner.is_running():
            return None
        raise RemotePairingError(ErrorCode.UNAVAILABLE, "Remote peer cleanup is pending")

    async def _cleanup_peer(self, session):
        if session.peer_id is None:
            return
        endpoint = await self._fresh_endpoint(session)
        if endpoint is not None:
            await self.engine.close(endpoint, session.peer_id)
        session.peer_id = None

    def _begin_retire(self, session):
        # Fence successors before any network await. The attempt and admission
        # remain retained even if DELETE or the broker release response is lost.
        session.canceled = True
        if session.mutation_guard is not None:
            session.mutation_guard.cancel()
        if self.current is session:
            self.current = None
        self._cleanup[session.session_id] = session
        if session.retirement is not None and not session.retirement.done():
            return session.retirement
        if session.task is not None and not session.task.done():
            session.task.cancel()
        session.retirement = self._track(self._finish_retire(session))
        return session.retirement

    async def _finish_retire(self, session):
        try:
            # An HTTP operation may complete after cancellation. Supervise it
            # until it settles, then DELETE its pre-published exact attempt.
            if session.task is not None:
                await asyncio.gather(session.task, return_exceptions=True)
            await self._cleanup_peer(session)
            if session.admission_attempted:
                await self.authority.release(session.session_id, session.generation)
                session.admission_attempted = False
            self._cleanup.pop(session.session_id, None)
        except Exception:
            raise RemotePairingError(ErrorCode.UNAVAILABLE, "Remote peer cleanup is pending") from None

    async def _retire(self, session):
        await asyncio.shield(self._begin_retire(session))

    async def select(self, device, serial, *, context, request_id, mutation_guard):
        self._authorized(device, context)
        if self._cleanup:
            raise RemotePairingError(ErrorCode.BUSY, "Previous remote peer is closing")
        previous = self.current
        self._generation += 1
        session = RemoteSelection(str(uuid.uuid4()), self._generation, device.id, serial, context,
                                  mutation_guard=mutation_guard, request_ids={request_id}, selected_at=self.clock())
        self.current = session
        try:
            if previous is not None:
                await self._retire(previous)
            selection = await self.actions.select(serial, "127.0.0.1", mutation_guard=mutation_guard)
            if not self._valid(session) or mutation_guard.is_canceled():
                raise RemotePairingError(ErrorCode.CANCELED, "Selection was canceled")
            session.endpoint = await asyncio.to_thread(self.endpoint, serial)
            if session.endpoint is None:
                raise RemotePairingError(ErrorCode.UNAVAILABLE, "Engine is unavailable")
            if not self._valid(session) or mutation_guard.is_canceled():
                raise RemotePairingError(ErrorCode.CANCELED, "Selection was canceled")
            session.admission_attempted = True
            try:
                bundles = await self.authority.authorize(request_id, session.session_id, session.generation)
            except RemotePairingError as exc:
                if exc.code not in {ErrorCode.OFFLINE, ErrorCode.TIMEOUT, ErrorCode.UNAVAILABLE, ErrorCode.CANCELED}:
                    session.admission_attempted = False  # Definite admission rejection.
                raise
            bundles = MediaBundles.model_validate(bundles)
            if not self._valid(session):
                raise RemotePairingError(ErrorCode.CANCELED, "Selection was canceled")
            session.host_ice = bundles.host.ice_servers
            self._disconnected_at = None
            public = {key: value for key, value in selection.items()
                      if key in {"ok", "id", "serial", "name", "w", "h", "tier"}}
            return {**public, "session_id": session.session_id, "generation": session.generation, **bundles.viewer.model_dump()}
        except BaseException:
            self._begin_retire(session)
            raise

    def _owned(self, device, session_id, generation, context):
        self._authorized(device, context)
        session = self.current
        if (session is None or session.session_id != session_id or session.generation != generation
                or session.device_id != device.id or session.context.viewer_id != context.viewer_id
                or session.context.host_epoch != context.host_epoch):
            raise RemotePairingError(ErrorCode.STALE_GENERATION, "Session generation is stale")
        return session

    async def negotiate(self, device, session_id, offer, deadline, *, generation, context, request_id):
        session = self._owned(device, session_id, generation, context)
        if session.negotiating or session.peer_id is not None:
            raise RemotePairingError(ErrorCode.BUSY, "Session already negotiated")
        session.negotiating = True
        session.request_ids.add(request_id)
        async def complete():
            endpoint = await self._fresh_endpoint(session)
            if endpoint is None or endpoint.generation != session.endpoint.generation:
                raise RemotePairingError(ErrorCode.STALE_GENERATION, "Engine generation changed")
            def on_attempt(peer_id):
                session.peer_id = peer_id
            answer = await self.engine.negotiate(endpoint, session_id, offer, session.host_ice, deadline,
                                                 on_attempt=on_attempt)
            if answer.peer_id != session.peer_id:
                raise RemotePairingError(ErrorCode.UNAVAILABLE, "Engine attempt changed")
            current = await self._fresh_endpoint(session)
            if (not self._valid(session) or current is None
                    or current.generation != session.endpoint.generation
                    or answer.generation != session.endpoint.generation):
                raise RemotePairingError(ErrorCode.STALE_GENERATION, "Session generation changed")
            if asyncio.get_running_loop().time() >= deadline:
                raise asyncio.TimeoutError
            return {"session_id": session_id, "generation": generation, "answer": answer.answer}
        session.task = self._track(complete())
        try:
            return await asyncio.shield(session.task)
        except BaseException:
            session.canceled = True
            self._begin_retire(session)
            raise

    async def close(self, device, session_id, *, generation, context):
        session = self._owned(device, session_id, generation, context)
        await self._retire(session)
        return {"closed": True}

    async def dispatch(self, command: Command, device: PairedDevice, *, context: RoutingContext,
                       mutation_guard: MutationGuard | None = None) -> dict:
        payload = command.payload
        if command.op == "select":
            return await self.select(device, payload["serial"], context=context, request_id=command.id, mutation_guard=mutation_guard)
        session = self._owned(device, payload["session_id"], payload["generation"], context)
        if command.op == "negotiate":
            deadline = asyncio.get_running_loop().time() + payload["timeout_ms"] / 1000
            return await self.negotiate(device, session.session_id, payload["offer"], deadline,
                                        generation=session.generation, context=context, request_id=command.id)
        if command.op == "close":
            return await self.close(device, session.session_id, generation=session.generation, context=context)
        if command.op == "renew":
            if self.clock() - session.selected_at < 3300:
                raise RemotePairingError(ErrorCode.BUSY, "Renewal is not due")
            return await self.select(device, session.serial, context=context, request_id=command.id, mutation_guard=mutation_guard)
        raise RemotePairingError(ErrorCode.INVALID_REQUEST, "Unsupported session operation")

    async def cancel(self, installation_id: str, host_epoch: int, viewer_id: str,
                     routing_id: str | None = None) -> None:
        session = self.current
        if (session is not None and installation_id == self.installation_id
                and (session.context.host_epoch, session.context.viewer_id) == (host_epoch, viewer_id)
                and (routing_id is None or routing_id in session.request_ids)):
            await self._retire(session)

    def disconnected(self) -> None:
        if self._disconnected_at is None:
            self._disconnected_at = self.clock()

    def connected(self) -> None:
        # A replacement signaling epoch must select/authorize again; existing
        # media remains only for the original bounded reconnect grace.
        if self.current is None:
            self._disconnected_at = None

    async def sweep(self, now: float) -> None:
        session = self.current
        if session is not None and (not self._valid(session)
                or (self._disconnected_at is not None and now - self._disconnected_at >= 60)):
            self._begin_retire(session)
        for session in list(self._cleanup.values()):
            self._begin_retire(session)

    async def drain(self) -> None:
        while self._tasks:
            tasks = tuple(self._tasks)
            await asyncio.gather(*tasks, return_exceptions=True)
            self._tasks.difference_update(tasks)

    async def shutdown(self) -> None:
        if self.current is not None:
            self._begin_retire(self.current)
        for session in list(self._cleanup.values()):
            self._begin_retire(session)
        await self.drain()
        await self.engine.aclose()
