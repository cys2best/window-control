import asyncio
import importlib
import json
import ssl
import uuid

import pytest
from websockets.asyncio.client import connect
from broker.identity_store import InstallationIdentity
from server.pairing import PairingStore
from tests.fixtures.fake_broker import local_broker


def wire(op, payload=None, request_id=None):
    return json.dumps({"v": 1, "id": request_id or str(uuid.uuid4()), "op": op, "payload": payload or {}})


class Actions:
    def __init__(self):
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.block = False

    async def instances(self):
        self.calls.append("instances")
        if self.block:
            self.started.set()
            await self.release.wait()
        return []


async def host(origin, identity):
    module = importlib.import_module("server.remote_client")
    from server.remote_dispatch import RemoteDispatcher
    pairing = PairingStore()
    token = pairing.pair(pairing.start_pairing(), "Phone")
    actions = Actions()
    client = module.RemoteHostClient(origin, identity, RemoteDispatcher(actions, pairing), allow_insecure_localhost=True)
    stop = asyncio.Event()
    task = asyncio.create_task(client.run(stop))
    await asyncio.wait_for(client.ready.wait(), 2)
    return client, stop, task, actions, pairing, token


async def viewer(origin, identity, token):
    ws = await connect(origin.replace("http", "ws") + "/connect")
    await ws.send(wire("viewer_auth", {"installation_id": identity.installation_id, "token": token}))
    reply = json.loads(await asyncio.wait_for(ws.recv(), 2))
    return ws, reply


@pytest.mark.asyncio
async def test_real_broker_pc_authorizes_before_admission_and_each_command(tmp_path):
    async with local_broker(tmp_path) as (origin, http, app):
        identity = InstallationIdentity(**(await http.post("/installations")).json())
        client, stop, task, actions, pairing, token = await host(origin, identity)
        try:
            ws, denied = await viewer(origin, identity, "bad")
            assert denied["error"]["code"] == "not_paired"
            await ws.close()
            assert actions.calls == []
            ws, approved = await viewer(origin, identity, token)
            assert approved["ok"] and approved["result"]["authenticated"]
            async with ws:
                await ws.send(wire("instances"))
                assert json.loads(await ws.recv())["ok"]
                pairing.remove_all()
                await ws.send(wire("instances"))
                assert json.loads(await ws.recv())["error"]["code"] == "not_paired"
            assert actions.calls == ["instances"]
        finally:
            stop.set()
            await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_pairing_close_precedes_success_without_discarding_result(tmp_path):
    async with local_broker(tmp_path) as (origin, http, app):
        identity = InstallationIdentity(**(await http.post("/installations")).json())
        client, stop, task, actions, pairing, token = await host(origin, identity)
        try:
            code = pairing.start_pairing()
            invitation = await asyncio.to_thread(client.remote_pairing.open)
            # A subsequent heartbeat proves ordered delivery of pairing_open.
            await asyncio.sleep(.05)
            async with connect(origin.replace("http", "ws") + "/connect") as ws:
                await ws.send(wire("pair", {"handle": invitation.handle, "code": code, "device_name": "New phone"}))
                result = json.loads(await asyncio.wait_for(ws.recv(), 2))
                assert result["ok"] and result["result"]["installation_id"] == identity.installation_id
                assert pairing.device_for_token(result["result"]["token"])
            assert client.remote_pairing.active_invitation() is None
            async with connect(origin.replace("http", "ws") + "/connect") as ws:
                await ws.send(wire("pair", {"handle": invitation.handle, "code": code}))
                assert json.loads(await ws.recv())["error"]["code"] == "expired_pairing"
        finally:
            stop.set()
            await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_stop_cancels_connection_and_does_not_block_gui():
    module = importlib.import_module("server.remote_client")
    from server.remote_dispatch import RemoteDispatcher
    entered = asyncio.Event()
    canceled = asyncio.Event()

    async def hanging_connect(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            canceled.set()

    dispatcher = RemoteDispatcher(Actions(), PairingStore())
    client = module.RemoteHostClient("https://service.example", InstallationIdentity(installation_id="test", credential="secret"), dispatcher, connector=hanging_connect)
    stop = asyncio.Event()
    task = asyncio.create_task(client.run(stop))
    await entered.wait()
    stop.set()
    await asyncio.wait_for(task, .5)
    assert canceled.is_set()
    assert not client.ready.is_set()


@pytest.mark.asyncio
async def test_invalid_service_certificate_fails_closed():
    module = importlib.import_module("server.remote_client")
    from server.remote_dispatch import RemoteDispatcher
    calls = []
    stop = asyncio.Event()

    async def bad_certificate(url, **kwargs):
        calls.append((url, kwargs))
        stop.set()
        raise ssl.SSLCertVerificationError("invalid service certificate")

    client = module.RemoteHostClient("https://service.example", InstallationIdentity(installation_id="test", credential="secret"), RemoteDispatcher(Actions(), PairingStore()), connector=bad_certificate)
    await client.run(stop)
    assert len(calls) == 1
    assert calls[0][0] == "wss://service.example/connect"
    assert calls[0][1]["ssl"].verify_mode == ssl.CERT_REQUIRED
    assert not client.ready.is_set()
    assert client.last_error == "Remote service certificate could not be verified"


@pytest.mark.asyncio
async def test_reconnect_drops_old_commands_and_reauthenticates(tmp_path):
    async with local_broker(tmp_path) as (origin, http, app):
        identity = InstallationIdentity(**(await http.post("/installations")).json())
        client, stop, task, actions, pairing, token = await host(origin, identity)
        try:
            actions.block = True
            ws, result = await viewer(origin, identity, token)
            await ws.send(wire("instances"))
            await asyncio.wait_for(actions.started.wait(), 2)
            epoch = client.host_epoch
            await client.connection.close()
            reply = json.loads(await asyncio.wait_for(ws.recv(), 2))
            assert not reply["ok"] and reply["error"]["code"] in ("offline", "stale_generation")
            await ws.close()
            for _ in range(300):
                if client.ready.is_set() and client.host_epoch > epoch:
                    break
                await asyncio.sleep(.01)
            assert client.host_epoch > epoch and client.ready.is_set()
            assert actions.calls == ["instances"]
            actions.block = False
            ws, approved = await viewer(origin, identity, token)
            assert approved["ok"]
            await ws.close()
        finally:
            stop.set()
            await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_offline_host_cannot_admit_viewer(tmp_path):
    async with local_broker(tmp_path) as (origin, http, app):
        identity = InstallationIdentity(**(await http.post("/installations")).json())
        ws, reply = await viewer(origin, identity, "anything")
        assert reply["error"]["code"] == "offline"
        await ws.close()


@pytest.mark.asyncio
async def test_owner_revocation_closes_broker_viewer_and_calls_media_hook(tmp_path):
    from websockets.exceptions import ConnectionClosed
    async with local_broker(tmp_path) as (origin, http, app):
        identity = InstallationIdentity(**(await http.post("/installations")).json())
        client, stop, task, actions, pairing, token = await host(origin, identity)
        closed = []
        client.on_device_invalidated = closed.append
        try:
            ws, result = await viewer(origin, identity, token)
            device = pairing.device_for_token(token)
            pairing.remove_device(device.id)
            with pytest.raises(ConnectionClosed):
                await asyncio.wait_for(ws.recv(), 2)
            assert closed == [device.id]
            assert client.ready.is_set()
        finally:
            stop.set()
            await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
async def test_remote_pairing_uses_same_five_attempt_budget_as_lan(tmp_path):
    async with local_broker(tmp_path) as (origin, http, app):
        identity = InstallationIdentity(**(await http.post("/installations")).json())
        client, stop, task, actions, pairing, token = await host(origin, identity)
        try:
            code = pairing.start_pairing()
            invitation = await asyncio.to_thread(client.remote_pairing.open)
            await asyncio.sleep(.05)
            wrong = "000000" if code != "000000" else "111111"
            for _ in range(4):
                assert pairing.pair(wrong, "LAN") is None
            async with connect(origin.replace("http", "ws") + "/connect") as ws:
                await ws.send(wire("pair", {"handle": invitation.handle, "code": wrong}))
                assert json.loads(await ws.recv())["error"]["code"] == "not_paired"
            assert pairing.active_code() is None
            assert client.remote_pairing.active_invitation() is None
        finally:
            stop.set()
            await asyncio.wait_for(task, 2)


def test_host_queue_is_bounded_and_shutdown_detaches_pairing():
    module = importlib.import_module("server.remote_client")
    from server.remote_dispatch import RemoteDispatcher
    from server.remote_pairing import RemotePairingError
    client = module.RemoteHostClient("https://service.example", InstallationIdentity(installation_id="test", credential="secret"), RemoteDispatcher(Actions(), PairingStore()))
    with pytest.raises(RemotePairingError):
        client.invalidate_device("device1")
    assert client._queue.maxsize == 32
    client.remote_pairing.shutdown()
    assert not client.dispatcher.pairing._window_closed


@pytest.mark.asyncio
async def test_unexpected_action_failure_does_not_leave_request_hanging(tmp_path):
    from remote_protocol import Reply
    async with local_broker(tmp_path) as (origin, http, app):
        identity = InstallationIdentity(**(await http.post("/installations")).json())
        client, stop, task, actions, pairing, token = await host(origin, identity)
        class Sessions:
            async def dispatch(self, command, device):
                return Reply(v=1, id=command.id, ok=True, result={"answer": "x" * (128 * 1024 + 1)})
        client.dispatcher.sessions = Sessions()
        try:
            ws, approved = await viewer(origin, identity, token)
            await ws.send(wire("renew", {"session_id": str(uuid.uuid4()), "generation": 1}))
            reply = json.loads(await asyncio.wait_for(ws.recv(), .5))
            assert reply["error"]["code"] == "unavailable"
            await ws.close()
        finally:
            stop.set()
            await asyncio.wait_for(task, 2)


def test_service_transport_requires_tls_and_explicit_localhost_test_mode():
    module = importlib.import_module("server.remote_client")
    from server.remote_identity import RemoteIdentityError
    from server.remote_dispatch import RemoteDispatcher
    identity = InstallationIdentity(installation_id="test", credential="secret")
    dispatcher = RemoteDispatcher(Actions(), PairingStore())
    for origin in ("http://remote.example", "http://127.0.0.1", "https://service.example?token=secret", "https://user:secret@service.example"):
        with pytest.raises(RemoteIdentityError):
            module.RemoteHostClient(origin, identity, dispatcher)
    with pytest.raises(RemoteIdentityError):
        module.RemoteHostClient("http://remote.example", identity, dispatcher, allow_insecure_localhost=True)


@pytest.mark.asyncio
async def test_shutdown_drops_live_invitation_without_resurrecting_on_reconnect(tmp_path):
    async with local_broker(tmp_path) as (origin, http, app):
        identity = InstallationIdentity(**(await http.post("/installations")).json())
        client, stop, task, actions, pairing, token = await host(origin, identity)
        pairing.start_pairing()
        invitation = await asyncio.to_thread(client.remote_pairing.open)
        epoch = client.host_epoch
        await client.connection.close()
        for _ in range(300):
            if client.ready.is_set() and client.host_epoch > epoch:
                break
            await asyncio.sleep(.01)
        assert client.ready.is_set()
        assert client.remote_pairing.active_invitation() is None
        assert pairing.active_code() is not None
        stop.set()
        await asyncio.wait_for(task, 2)
        assert not pairing._window_closed


@pytest.mark.asyncio
async def test_internal_routing_forgery_and_queue_rejection_fail_closed():
    from remote_protocol import RoutedCommand, RoutingContext
    from server.remote_dispatch import RemoteDispatcher
    from server.remote_pairing import RemotePairingError
    module = importlib.import_module("server.remote_client")
    identity = InstallationIdentity(installation_id="host", credential="secret")
    actions = Actions()
    client = module.RemoteHostClient("https://service.example", identity, RemoteDispatcher(actions, PairingStore()))
    client.host_epoch = 1
    client._loop = asyncio.get_running_loop()
    client._wake = asyncio.Event()
    client._accepting = True
    for _ in range(32):
        client.invalidate_device("device1")
    with pytest.raises(RemotePairingError, match="full"):
        client.invalidate_device("device1")
    assert client._queue.qsize() == 32
    while not client._queue.empty():
        client._queue.get_nowait()
    for context in (RoutingContext(installation_id="foreign", host_epoch=1, viewer_id="phone", token="secret"), RoutingContext(installation_id="host", host_epoch=2, viewer_id="phone", token="secret")):
        frame = RoutedCommand(v=1, id=str(uuid.uuid4()), op="instances", payload={}, context=context).model_dump_json()
        class Connection:
            def __aiter__(self):
                async def frames():
                    yield frame
                return frames()
        client.connection = Connection()
        with pytest.raises(ValueError, match="ownership"):
            await client._connected()
    assert actions.calls == []
