import asyncio
import importlib
import uuid
from types import SimpleNamespace

import pytest
from remote_protocol import Command, RoutingContext
from server.instance_manager import MutationGuard
from server.pairing import PairingStore
from server.engine_remote import RemoteEngineEndpoint, RemotePeerAnswer


class Actions:
    async def select(self, serial, advertised_host, *, mutation_guard):
        assert not mutation_guard.is_canceled()
        return dict(ok=True, id="adb:" + serial, name="Window", serial=serial, generation=0, tier="720", w=1280, h=720,
                    whep_token="never-public", whep_url="never-public")


class Engine:
    def __init__(self):
        self.live = set()
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False
        self.generation = 0
        self.closed = []
        self.owner = SimpleNamespace(is_running=lambda: True)

    def endpoint(self, serial):
        return RemoteEngineEndpoint(1234 if serial == "a" else 1235, "fresh", self.generation, self.owner)

    async def negotiate(self, endpoint, session_id, offer, ice_servers, deadline, *, on_attempt=None):
        peer = uuid.uuid4().hex
        if on_attempt is not None:
            on_attempt(peer)
        self.started.set()
        if self.block:
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                await self.release.wait()
        self.live.add(peer)
        return RemotePeerAnswer(peer, "v=0\r\n", endpoint.generation)

    async def close(self, endpoint, peer_id):
        self.live.discard(peer_id)
        self.closed.append((endpoint, peer_id))

    async def aclose(self):
        pass


class Authority:
    def __init__(self):
        self.released = []
        self.clock = lambda: 1000.0

    async def authorize(self, routing_id, session_id, generation):
        from remote_protocol import MediaBundles, SessionIceBundle
        def bundle():
            return SessionIceBundle(ice_servers=[{"urls": ["stun:stun.example:3478"]}],
                                    expires_at=int(self.clock()) + 3600, renew_after=3300,
                                    relay_available=False)
        return MediaBundles(host=bundle(), viewer=bundle())

    async def release(self, session_id, generation):
        self.released.append(session_id)


def setup():
    module = importlib.import_module("server.remote_sessions")
    pairing = PairingStore()
    token = pairing.pair(pairing.start_pairing(), "Phone")
    engine, authority = Engine(), Authority()
    now = [1000.]
    authority.clock = lambda: now[0]
    sessions = module.RemoteSessionCoordinator("installation", Actions(), pairing, engine.endpoint, engine,
                                              authority=authority, clock=lambda: now[0])
    context = RoutingContext(installation_id="installation", host_epoch=1, viewer_id="viewer", token=token)
    return sessions, engine, authority, pairing, context, now


async def dispatch(sessions, context, op, payload):
    command = Command(v=1, id=str(uuid.uuid4()), op=op, payload=payload)
    device = sessions.pairing.device_for_token(context.token)
    return await sessions.dispatch(command, device, context=context, mutation_guard=MutationGuard())


async def select(sessions, context, serial="a"):
    return await dispatch(sessions, context, "select", {"serial": serial})


async def negotiate(sessions, context, selection):
    return await dispatch(sessions, context, "negotiate", {"session_id": selection["session_id"],
                          "generation": selection["generation"], "offer": "v=0\r\n", "timeout_ms": 1000})


@pytest.mark.asyncio
async def test_select_replaces_previous_remote_across_instances():
    s, e, auth, pairing, context, now = setup()
    first = await select(s, context)
    assert first["generation"] > 0
    assert "whep_token" not in first and "whep_url" not in first
    await negotiate(s, context, first)
    assert len(e.live) == 1
    second = await select(s, context, "b")
    assert second["session_id"] != first["session_id"]
    assert not e.live and first["session_id"] in auth.released
    await negotiate(s, context, second)
    assert len(e.live) == 1
    await s.shutdown()


@pytest.mark.asyncio
async def test_cancel_before_answer_closes_late_peer():
    s, e, _, _, context, _ = setup()
    first = await select(s, context)
    e.block = True
    task = asyncio.create_task(negotiate(s, context, first))
    await e.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    e.release.set()
    await s.drain()
    assert not e.live and len(e.closed) == 1
    await s.shutdown()


@pytest.mark.asyncio
async def test_stale_generation_cannot_adopt():
    s, e, _, _, context, _ = setup()
    first = await select(s, context)
    e.block = True
    task = asyncio.create_task(negotiate(s, context, first))
    await e.started.wait()
    e.generation = 1
    e.release.set()
    with pytest.raises(Exception, match="generation"):
        await task
    await s.drain()
    assert not e.live
    await s.shutdown()


@pytest.mark.asyncio
async def test_revocation_sweep_closes_within_five_seconds():
    s, e, _, pairing, context, now = setup()
    first = await select(s, context)
    await negotiate(s, context, first)
    pairing.remove_all()
    now[0] += 1
    await s.sweep(now[0])
    await s.drain()
    assert not e.live
    with pytest.raises(Exception, match="paired"):
        await select(s, context)
    await s.shutdown()


@pytest.mark.asyncio
async def test_renewal_revalidates_token_and_replaces_old_allocation():
    s, e, _, pairing, context, now = setup()
    first = await select(s, context)
    await negotiate(s, context, first)
    now[0] += 3300
    second = await dispatch(s, context, "renew", {"session_id": first["session_id"], "generation": first["generation"]})
    assert second["session_id"] != first["session_id"] and not e.live
    await negotiate(s, context, second)
    pairing.remove_all()
    with pytest.raises(Exception, match="paired"):
        await dispatch(s, context, "renew", {"session_id": second["session_id"], "generation": second["generation"]})
    await s.shutdown()


@pytest.mark.asyncio
async def test_disconnect_grace_expires_at_sixty_seconds():
    s, e, _, _, context, now = setup()
    await negotiate(s, context, await select(s, context))
    s.disconnected()
    now[0] += 59
    await s.sweep(now[0])
    assert e.live
    now[0] += 1
    await s.sweep(now[0])
    await s.drain()
    assert not e.live
    await s.shutdown()


@pytest.mark.asyncio
async def test_completed_viewer_cancel_cannot_close_successor():
    sessions, engine, _, _, context, _ = setup()
    try:
        first = await select(sessions, context)
        await negotiate(sessions, context, first)
        await sessions.cancel(context.installation_id, context.host_epoch, context.viewer_id)
        await sessions.drain()
        assert engine.live == set()
        successor_context = context.model_copy(update={"viewer_id": "successor"})
        second = await select(sessions, successor_context)
        await negotiate(sessions, successor_context, second)
        successor_peers = set(engine.live)
        await sessions.cancel(context.installation_id, context.host_epoch, context.viewer_id)
        await sessions.drain()
        assert engine.live == successor_peers
        assert len(engine.live) == 1
    finally:
        await sessions.shutdown()


@pytest.mark.asyncio
async def test_socket_reconnect_without_fresh_approval_does_not_extend_grace():
    sessions, engine, _, _, context, now = setup()
    try:
        await negotiate(sessions, context, await select(sessions, context))
        sessions.disconnected()
        now[0] += 59
        sessions.connected()
        await sessions.sweep(now[0])
        assert len(engine.live) == 1
        sessions.disconnected()
        now[0] += 1
        await sessions.sweep(now[0])
        await sessions.drain()
        assert engine.live == set()
    finally:
        await sessions.shutdown()


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch,code", [("viewer", "stale_generation"), ("epoch", "stale_generation"),
                                          ("revision", "stale_generation"), ("device", "not_paired")])
async def test_foreign_ownership_cannot_create_native_peer(mismatch, code):
    from server.remote_pairing import RemotePairingError
    sessions, engine, _, _, context, _ = setup()
    try:
        selection = await select(sessions, context)
        if mismatch == "revision":
            selection = {**selection, "generation": selection["generation"] + 1}
        else:
            context = context.model_copy(update={
                "viewer": {"viewer_id": "other"}, "epoch": {"host_epoch": 2},
                "device": {"token": "revoked"}}[mismatch])
        with pytest.raises(RemotePairingError) as error:
            await negotiate(sessions, context, selection)
        assert error.value.code.value == code
        assert not engine.started.is_set() and not engine.live
    finally:
        await sessions.shutdown()


@pytest.mark.asyncio
async def test_lost_adopted_answer_and_failed_delete_retain_admission_until_retry():
    from server.engine_remote import EngineRemoteError
    from server.remote_pairing import RemotePairingError
    sessions, engine, authority, _, context, now = setup()
    async def lost_answer(endpoint, session_id, offer, ice_servers, deadline, *, on_attempt):
        peer = uuid.uuid4().hex
        on_attempt(peer)
        engine.live.add(peer)
        raise EngineRemoteError("engine negotiation failed; cleanup may require retry")
    original_close = engine.close
    async def failed_delete(endpoint, peer_id):
        raise EngineRemoteError("engine cleanup failed")
    engine.negotiate, engine.close = lost_answer, failed_delete
    try:
        first = await select(sessions, context)
        with pytest.raises(EngineRemoteError):
            await negotiate(sessions, context, first)
        await sessions.drain()
        assert len(engine.live) == 1 and authority.released == []
        with pytest.raises(RemotePairingError) as error:
            await select(sessions, context, "b")
        assert error.value.code.value == "busy"
        engine.close = original_close
        await sessions.sweep(now[0] + 1)
        await sessions.drain()
        assert not engine.live and authority.released == [first["session_id"]]
        assert (await select(sessions, context, "b"))["generation"] > first["generation"]
    finally:
        engine.close = original_close
        await sessions.shutdown()


@pytest.mark.asyncio
async def test_live_owner_reusing_port_never_receives_old_capability_and_fences_successor():
    from server.remote_pairing import RemotePairingError
    sessions, engine, authority, _, context, now = setup()
    running = [True]
    retained = SimpleNamespace(is_running=lambda: running[0])
    engine.owner = retained
    try:
        first = await select(sessions, context)
        await negotiate(sessions, context, first)
        engine.owner = SimpleNamespace(is_running=lambda: True)
        with pytest.raises(RemotePairingError):
            await sessions.cancel(context.installation_id, context.host_epoch, context.viewer_id)
        await sessions.drain()
        assert not engine.closed and not authority.released and len(engine.live) == 1
        with pytest.raises(RemotePairingError) as error:
            await select(sessions, context, "b")
        assert error.value.code.value == "busy"
        running[0] = False
        engine.live.clear()  # The retained native process exited.
        await sessions.sweep(now[0] + 1)
        await sessions.drain()
        assert not engine.closed and authority.released == [first["session_id"]]
        await select(sessions, context, "b")
    finally:
        running[0] = False
        await sessions.shutdown()


@pytest.mark.asyncio
async def test_missing_endpoint_is_not_proof_live_owner_exited():
    from server.remote_pairing import RemotePairingError
    sessions, engine, authority, _, context, now = setup()
    first = await select(sessions, context)
    await negotiate(sessions, context, first)
    sessions.endpoint = lambda serial: None
    with pytest.raises(RemotePairingError):
        await sessions.cancel(context.installation_id, context.host_epoch, context.viewer_id)
    await sessions.drain()
    assert engine.live and not authority.released
    sessions.endpoint = engine.endpoint
    await sessions.sweep(now[0] + 1)
    await sessions.drain()
    assert not engine.live and authority.released == [first["session_id"]]
    await sessions.shutdown()


@pytest.mark.asyncio
async def test_replaced_selection_cannot_authorize_after_blocked_endpoint_returns():
    import threading
    from server.remote_pairing import RemotePairingError
    sessions, engine, authority, _, context, _ = setup()
    entered, release = threading.Event(), threading.Event()
    authorizations = []
    original_authorize = authority.authorize
    async def authorize(routing_id, session_id, generation):
        authorizations.append(session_id)
        return await original_authorize(routing_id, session_id, generation)
    authority.authorize = authorize
    def endpoint(serial):
        if serial == "a":
            entered.set()
            assert release.wait(2)
        return engine.endpoint(serial)
    sessions.endpoint = endpoint
    old = asyncio.create_task(select(sessions, context))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        successor = await select(sessions, context.model_copy(update={"viewer_id": "successor"}), "b")
        release.set()
        with pytest.raises(RemotePairingError):
            await old
        await sessions.drain()
        assert authorizations == [successor["session_id"]]
    finally:
        release.set()
        await asyncio.gather(old, return_exceptions=True)
        await sessions.shutdown()
