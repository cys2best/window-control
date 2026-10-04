"""Real broker/socket/host lifecycle with a controlled external engine boundary."""
import asyncio
import json
import time
import uuid

import pytest
from broker.identity_store import InstallationIdentity
from tests.fixtures.fake_broker import local_broker
from tests.test_remote_client import host, viewer, wire
from tests.test_remote_sessions import Actions, Engine


async def command(ws, op, payload):
    await ws.send(wire(op, payload))
    return json.loads(await asyncio.wait_for(ws.recv(), 2))


async def working_peer(ws):
    selected = await command(ws, "select", {"serial": "a"})
    assert selected["ok"], selected
    selection = selected["result"]
    answer = await command(ws, "negotiate", {"session_id": selection["session_id"],
                        "generation": selection["generation"], "offer": "v=0\r\n", "timeout_ms": 1000})
    assert answer["ok"], answer
    assert set(answer["result"]) == {"session_id", "generation", "answer"}
    assert not {"whep_url", "whep_token", "capability", "host"} & selection.keys()
    return selection


async def eventually(predicate, timeout=2):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.01)


@pytest.mark.asyncio
async def test_completed_command_viewer_disconnect_closes_media_and_preserves_successor(tmp_path):
    async with local_broker(tmp_path) as (origin, http, app):
        identity = InstallationIdentity(**(await http.post("/installations")).json())
        engine = Engine()
        client, stop, task, _, _, token = await host(origin, identity, Actions(), engine=engine)
        a, _ = await viewer(origin, identity, token)
        b = None
        try:
            await working_peer(a)
            assert len(engine.live) == 1
            await a.close()
            await eventually(lambda: not engine.live)
            b, _ = await viewer(origin, identity, token)
            await working_peer(b)
            assert len(engine.live) == 1 and client.ready.is_set()
        finally:
            await a.close()
            if b is not None:
                await b.close()
            stop.set()
            await asyncio.wait_for(task, 2)
        assert not engine.live


@pytest.mark.asyncio
@pytest.mark.parametrize("offline", [False, True])
async def test_application_lifetime_sweep_revokes_media_during_socket_outage(tmp_path, offline):
    async with local_broker(tmp_path) as (origin, http, app):
        identity = InstallationIdentity(**(await http.post("/installations")).json())
        engine = Engine()
        client, stop, task, _, pairing, token = await host(origin, identity, Actions(), engine=engine)
        ws, _ = await viewer(origin, identity, token)
        try:
            await working_peer(ws)
            if offline:
                async def disconnected_connector(*args, **kwargs):
                    await asyncio.Event().wait()
                client.connector = disconnected_connector
                await client.connection.close()
                await eventually(lambda: not client.ready.is_set())
            started = time.monotonic()
            pairing.remove_all()
            await eventually(lambda: not engine.live, 5)
            assert time.monotonic() - started <= 5
            if offline:
                assert not client.ready.is_set()
        finally:
            await ws.close()
            stop.set()
            await asyncio.wait_for(task, 2)
        assert not engine.live


@pytest.mark.asyncio
async def test_reauthenticated_select_replaces_old_epoch_peer_and_original_grace_expires(tmp_path):
    async with local_broker(tmp_path) as (origin, http, app):
        identity = InstallationIdentity(**(await http.post("/installations")).json())
        engine, now = Engine(), [1000.]
        client, stop, task, _, _, token = await host(origin, identity, Actions(), engine=engine, clock=lambda: now[0])
        a, _ = await viewer(origin, identity, token)
        b = None
        try:
            first = await working_peer(a)
            old_peers = set(engine.live)
            epoch = client.host_epoch
            await client.connection.close()
            await eventually(lambda: client.ready.is_set() and client.host_epoch > epoch, 3)
            now[0] += 59
            await asyncio.sleep(1.05)
            assert engine.live == old_peers
            b, _ = await viewer(origin, identity, token)
            second = await working_peer(b)
            assert second["session_id"] != first["session_id"]
            assert len(engine.live) == 1 and not old_peers & engine.live
            client.connector = lambda *a, **kw: asyncio.Event().wait()
            await client.connection.close()
            await eventually(lambda: not client.ready.is_set())
            now[0] += 59
            await asyncio.sleep(1.05)
            assert len(engine.live) == 1
            now[0] += 1
            await eventually(lambda: not engine.live)
        finally:
            await a.close()
            if b is not None:
                await b.close()
            stop.set()
            await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_renewal_uses_distinct_endpoint_credentials_and_revoked_token_cannot_renew(tmp_path):
    async with local_broker(tmp_path) as (origin, http, app):
        app.state.media.settings.turn_urls = ["turn:relay.example:3478"]
        identity = InstallationIdentity(**(await http.post("/installations")).json())
        engine, now = Engine(), [1000.]
        client, stop, task, _, pairing, token = await host(origin, identity, Actions(), engine=engine, clock=lambda: now[0])
        ws, _ = await viewer(origin, identity, token)
        try:
            first = await working_peer(ws)
            host_ice = client.dispatcher.sessions.current.host_ice
            assert host_ice[0]["credential"] != first["ice_servers"][0]["credential"]
            now[0] += 3300
            result = await command(ws, "renew", {"session_id": first["session_id"], "generation": first["generation"]})
            assert result["ok"], result
            second = result["result"]
            assert not engine.live and second["session_id"] != first["session_id"]
            assert second["generation"] > first["generation"]
            assert second["ice_servers"][0]["credential"] != first["ice_servers"][0]["credential"]
            assert client.dispatcher.sessions.current.host_ice[0]["credential"] != second["ice_servers"][0]["credential"]
            pairing.remove_all()
            denied = await command(ws, "renew", {"session_id": second["session_id"], "generation": second["generation"]})
            assert denied["error"]["code"] == "not_paired"
        finally:
            await ws.close()
            stop.set()
            await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_production_factory_connects_manager_endpoint_and_owns_http_shutdown():
    from types import SimpleNamespace
    from server.pairing import PairingStore
    import main
    endpoint = Engine().endpoint
    actions = SimpleNamespace(manager=SimpleNamespace(_engine_orchestrator=SimpleNamespace(remote_endpoint=endpoint)))
    class Store:
        def load_or_register(self, service_url):
            return InstallationIdentity(installation_id="pc", credential="private")
    client = await main.build_remote_client("https://service.example", actions, PairingStore(),
                                            lambda *args: None, identity_store=Store())
    assert client is not None
    sessions = client.dispatcher.sessions
    assert sessions is not None and sessions.authority is client and sessions.endpoint is endpoint
    await sessions.shutdown()
    assert sessions.engine._http.is_closed
    client.remote_pairing.shutdown()


@pytest.mark.asyncio
async def test_lost_native_answer_retries_exact_delete_before_broker_readmission(tmp_path):
    import httpx
    from server.engine_remote import EngineRemoteClient, RemoteEngineEndpoint
    engine = Engine()
    fail_delete = [True]
    attempts, deletions = [], []
    def transport(request):
        if request.method == "POST":
            attempt = json.loads(request.content)["peer_id"]
            attempts.append(attempt)
            engine.live.add(attempt)
            raise httpx.ReadError("lost engine response")
        attempt = request.url.path.rsplit("/", 1)[-1]
        deletions.append(attempt)
        if fail_delete[0]:
            return httpx.Response(503)
        engine.live.discard(attempt)
        return httpx.Response(204)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http_engine:
        adapter = EngineRemoteClient(http_engine)
        engine.negotiate, engine.close, engine.aclose = adapter.negotiate, adapter.close, adapter.aclose
        async with local_broker(tmp_path) as (origin, http, app):
            identity = InstallationIdentity(**(await http.post("/installations")).json())
            client, stop, task, _, _, token = await host(origin, identity, Actions(), engine=engine)
            ws, _ = await viewer(origin, identity, token)
            try:
                first = (await command(ws, "select", {"serial": "a"}))["result"]
                result = await command(ws, "negotiate", {"session_id": first["session_id"],
                            "generation": first["generation"], "offer": "v=0\r\n", "timeout_ms": 1000})
                assert result["error"]["code"] == "unavailable"
                assert len(engine.live) == 1
                blocked = await command(ws, "select", {"serial": "b"})
                assert blocked["error"]["code"] == "busy"
                assert client.ready.is_set()
                fail_delete[0] = False
                await eventually(lambda: not engine.live)
                await eventually(lambda: not client.dispatcher.sessions._cleanup)
                successor = await command(ws, "select", {"serial": "b"})
                assert successor["ok"], successor
                assert set(deletions) == set(attempts) and len(attempts) == 1
            finally:
                fail_delete[0] = False
                await ws.close()
                stop.set()
                await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("rejected", [False, True])
async def test_dropped_authority_reply_releases_unknown_admission_without_disconnect(tmp_path, rejected):
    from websockets.asyncio.client import connect
    from server.remote_dispatch import RemoteDispatcher
    from server.remote_client import RemoteHostClient
    from server.remote_sessions import RemoteSessionCoordinator
    from server.pairing import PairingStore
    dropped = []
    authority_ids = set()
    drop_authority = [False]
    class FilteredConnection:
        def __init__(self, socket):
            self.socket = socket
        async def send(self, raw):
            message = json.loads(raw)
            if message.get("op") == "media_authorize":
                authority_ids.add(message["id"])
            await self.socket.send(raw)
        async def recv(self):
            return await self.socket.recv()
        async def close(self):
            await self.socket.close()
        def __aiter__(self):
            async def frames():
                async for raw in self.socket:
                    message = json.loads(raw)
                    if drop_authority[0] and message["id"] in authority_ids:
                        dropped.append(message["id"])
                        continue
                    yield raw
            return frames()
    async def connector(url, **options):
        return FilteredConnection(await connect(url, **options))
    async with local_broker(tmp_path) as (origin, http, app):
        identity = InstallationIdentity(**(await http.post("/installations")).json())
        pairing, engine = PairingStore(), Engine()
        token = pairing.pair(pairing.start_pairing(), "Phone")
        client = RemoteHostClient(origin, identity, RemoteDispatcher(Actions(), pairing),
                                  connector=connector, allow_insecure_localhost=True)
        sessions = RemoteSessionCoordinator(identity.installation_id, client.dispatcher.actions, pairing,
                                             engine.endpoint, engine, authority=client)
        client.dispatcher.sessions = sessions
        stop = asyncio.Event()
        task = asyncio.create_task(client.run(stop))
        await asyncio.wait_for(client.ready.wait(), 2)
        ws, _ = await viewer(origin, identity, token)
        epoch = client.host_epoch
        try:
            if rejected:
                # Spend the actual twelve-per-hour broker issuance allowance.
                for _ in range(12):
                    selected = await command(ws, "select", {"serial": "a"})
                    assert selected["ok"], selected
                    selected = selected["result"]
                    closed = await command(ws, "close", {"session_id": selected["session_id"],
                                                          "generation": selected["generation"]})
                    assert closed["ok"], closed
            drop_authority[0] = True
            await ws.send(wire("select", {"serial": "a"}))
            result = json.loads(await asyncio.wait_for(ws.recv(), 7))
            assert result["error"]["code"] == "timeout" and len(dropped) == 1
            await eventually(lambda: not sessions._cleanup)
            assert not engine.live and client.ready.is_set() and client.host_epoch == epoch
            assert identity.installation_id not in app.state.media.admissions
        finally:
            await ws.close()
            stop.set()
            await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_private_authority_rejection_preserves_other_viewers_and_epoch(tmp_path):
    from server.remote_pairing import RemotePairingError
    from tests.test_remote_client import Actions as ControlActions
    async with local_broker(tmp_path) as (origin, http, app):
        identity = InstallationIdentity(**(await http.post("/installations")).json())
        client, stop, task, _, _, token = await host(origin, identity, ControlActions())
        ws, _ = await viewer(origin, identity, token)
        epoch = client.host_epoch
        try:
            with pytest.raises(RemotePairingError) as rejected:
                await client.authorize(str(uuid.uuid4()), str(uuid.uuid4()), 1)
            assert rejected.value.code.value == "invalid_request"
            assert (await command(ws, "instances", {}))["ok"]
            assert client.ready.is_set() and client.host_epoch == epoch
        finally:
            await ws.close()
            stop.set()
            await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_lost_host_answer_after_completed_command_closes_retained_peer(tmp_path):
    from websockets.asyncio.client import connect
    from server.remote_client import RemoteHostClient
    from server.remote_dispatch import RemoteDispatcher
    from server.remote_sessions import RemoteSessionCoordinator
    from server.pairing import PairingStore
    dropped = asyncio.Event()
    class LostAnswerConnection:
        def __init__(self, socket):
            self.socket = socket
        async def send(self, raw):
            message = json.loads(raw)
            if "answer" in message.get("result", {}):
                dropped.set()
                return
            await self.socket.send(raw)
        async def recv(self):
            return await self.socket.recv()
        async def close(self):
            await self.socket.close()
        def __aiter__(self):
            return self.socket.__aiter__()
    async def connector(url, **options):
        return LostAnswerConnection(await connect(url, **options))
    async with local_broker(tmp_path) as (origin, http, app):
        identity = InstallationIdentity(**(await http.post("/installations")).json())
        pairing, engine = PairingStore(), Engine()
        token = pairing.pair(pairing.start_pairing(), "Phone")
        client = RemoteHostClient(origin, identity, RemoteDispatcher(Actions(), pairing),
                                  connector=connector, allow_insecure_localhost=True)
        sessions = RemoteSessionCoordinator(identity.installation_id, client.dispatcher.actions, pairing,
                                             engine.endpoint, engine, authority=client)
        client.dispatcher.sessions = sessions
        stop = asyncio.Event()
        task = asyncio.create_task(client.run(stop))
        await asyncio.wait_for(client.ready.wait(), 2)
        ws, _ = await viewer(origin, identity, token)
        try:
            first = (await command(ws, "select", {"serial": "a"}))["result"]
            await ws.send(wire("negotiate", {"session_id": first["session_id"], "generation": first["generation"],
                                            "offer": "v=0\r\n", "timeout_ms": 1000}))
            await asyncio.wait_for(dropped.wait(), 1)
            assert len(engine.live) == 1 and sessions.current.task.done()
            result = json.loads(await asyncio.wait_for(ws.recv(), 2))
            assert result["error"]["code"] == "timeout"
            await eventually(lambda: not engine.live)
            await eventually(lambda: not sessions._cleanup)
            assert client.ready.is_set()
            assert (await command(ws, "select", {"serial": "b"}))["ok"]
        finally:
            await ws.close()
            stop.set()
            await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_broker_restart_allows_fresh_select_after_confirmed_predecessor_delete(tmp_path):
    async with local_broker(tmp_path) as (origin, http, app):
        identity = InstallationIdentity(**(await http.post("/installations")).json())
        engine = Engine()
        client, stop, task, _, _, token = await host(origin, identity, Actions(), engine=engine)
        a, _ = await viewer(origin, identity, token)
        b = None
        try:
            await working_peer(a)
            old_peers = set(engine.live)
            # A fresh real broker loads durable identity, drops transient media
            # state and starts its host epochs again, as a service restart does.
            async with local_broker(tmp_path) as (new_origin, _, restarted):
                previous_socket = client.connection
                client.url = new_origin.replace("http", "ws") + "/connect"
                await previous_socket.close()
                await eventually(lambda: client.ready.is_set() and client.connection is not previous_socket, 3)
                b, approved = await viewer(new_origin, identity, token)
                assert approved["ok"]
                await working_peer(b)
                assert len(engine.live) == 1 and not old_peers & engine.live
                await b.close()
                b = None
        finally:
            await a.close()
            if b is not None:
                await b.close()
            stop.set()
            await asyncio.wait_for(task, 2)
