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


async def host(origin, identity, actions=None):
    module = importlib.import_module("server.remote_client")
    from server.remote_dispatch import RemoteDispatcher
    pairing = PairingStore()
    token = pairing.pair(pairing.start_pairing(), "Phone")
    actions = actions if actions is not None else Actions()
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


@pytest.mark.asyncio
@pytest.mark.parametrize("abandon", ["disconnect", "timeout"])
async def test_late_reply_from_one_viewer_preserves_other_viewer_and_invitation(tmp_path, monkeypatch, abandon):
    class SlowFirstActions(Actions):
        async def instances(self):
            self.calls.append("instances")
            if len(self.calls) == 1:
                self.started.set()
                await self.release.wait()
            return []

    if abandon == "timeout":
        monkeypatch.setattr("broker.app.COMMAND_TIMEOUT_SECONDS", .08, raising=False)
    async with local_broker(tmp_path) as (origin, http, app):
        identity = InstallationIdentity(**(await http.post("/installations")).json())
        actions = SlowFirstActions()
        client, stop, task, actions, pairing, token = await host(origin, identity, actions)
        a, approved = await viewer(origin, identity, token)
        b, approved = await viewer(origin, identity, token)
        epoch = client.host_epoch
        try:
            pairing.start_pairing()
            invitation = await asyncio.to_thread(client.remote_pairing.open)
            await a.send(wire("instances"))
            await asyncio.wait_for(actions.started.wait(), 1)
            if abandon == "disconnect":
                await a.close()
            else:
                reply = json.loads(await asyncio.wait_for(a.recv(), .4))
                assert reply["error"]["code"] == "timeout"
            actions.release.set()
            await asyncio.sleep(.05)
            assert client.ready.is_set() and client.host_epoch == epoch
            assert client.remote_pairing.active_invitation() == invitation
            await b.send(wire("instances"))
            assert json.loads(await asyncio.wait_for(b.recv(), .5))["ok"]
        finally:
            actions.release.set()
            await a.close()
            await b.close()
            stop.set()
            await asyncio.wait_for(task, 2)


def blocking_instance_actions(monkeypatch, operation):
    import threading
    from server.instance_manager import InstanceManager, Instance
    from server.remote_dispatch import InstanceActions
    from server.engine_runtime import EngineSelection
    started = threading.Event()
    release = threading.Event()
    replacement_started = threading.Event()
    calls = []
    before_replacement = []
    manager = None

    class BlockingOrchestrator:
        def call(self, *description):
            calls.append(description)
            if len(calls) == 1:
                started.set()
                assert release.wait(5)
            else:
                before_replacement.append((manager.active.serial, manager.get("emulator-5554").tier))
                replacement_started.set()

        def select(self, serial, advertised_host):
            self.call("select", serial)
            return EngineSelection("http://private", "secret", 1, 100, 200)

        def set_tier(self, serial, tier):
            self.call("quality", serial, tier)
            return True

    monkeypatch.setattr(InstanceManager, "_watchdog", lambda self: None)
    manager = InstanceManager(BlockingOrchestrator())
    for i, serial in enumerate(("emulator-5554", "emulator-5556")):
        manager._instances[serial] = Instance({"id": "adb:" + serial, "title": "LDPlayer", "ldplayer_index": i}, 100, 200)
    manager._active_serial = "emulator-5556"
    manager.get("emulator-5554").tier = "1080"
    return InstanceActions(manager), started, release, replacement_started, calls, before_replacement


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["select", "quality"])
async def test_reconnect_fences_blocked_synchronous_mutation_and_serializes_replacement(tmp_path, monkeypatch, operation):
    actions, started, release, replacement_started, calls, before = blocking_instance_actions(monkeypatch, operation)
    async with local_broker(tmp_path) as (origin, http, app):
        identity = InstallationIdentity(**(await http.post("/installations")).json())
        client, stop, task, actions, pairing, token = await host(origin, identity, actions)
        a, approved = await viewer(origin, identity, token)
        b = None
        epoch = client.host_epoch
        old = {"serial": "emulator-5554"}
        new = {"serial": "emulator-5556"}
        if operation == "quality":
            old["tier"] = "360"
            new = {"serial": "emulator-5554", "tier": "720"}
        try:
            await a.send(wire(operation, old))
            assert await asyncio.to_thread(started.wait, 1)
            await client.connection.close()
            for _ in range(300):
                if client.ready.is_set() and client.host_epoch > epoch:
                    break
                await asyncio.sleep(.01)
            assert client.ready.is_set() and client.host_epoch > epoch
            b, approved = await viewer(origin, identity, token)
            await b.send(wire(operation, new))
            await asyncio.sleep(.05)
            assert not replacement_started.is_set(), "Replacement engine mutation raced a surviving old worker"
            assert actions.manager.list_instances()  # Metadata stays available while engine work blocks.
            release.set()
            assert json.loads(await asyncio.wait_for(b.recv(), 1))["ok"]
            assert before == [("emulator-5556", "1080")], "Old epoch committed metadata before replacement"
            assert actions.manager.active.serial == "emulator-5556"
            assert actions.manager.get("emulator-5554").tier == ("720" if operation == "quality" else "1080")
            assert len(calls) == 2
        finally:
            release.set()
            await a.close()
            if b is not None:
                await b.close()
            stop.set()
            await asyncio.wait_for(task, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["select", "quality"])
async def test_stop_invalidates_blocked_synchronous_mutation_without_waiting_for_worker(tmp_path, monkeypatch, operation):
    actions, started, release, replacement_started, calls, before = blocking_instance_actions(monkeypatch, operation)
    async with local_broker(tmp_path) as (origin, http, app):
        identity = InstallationIdentity(**(await http.post("/installations")).json())
        client, stop, task, actions, pairing, token = await host(origin, identity, actions)
        ws, approved = await viewer(origin, identity, token)
        payload = {"serial": "emulator-5554"}
        if operation == "quality":
            payload["tier"] = "360"
        try:
            await ws.send(wire(operation, payload))
            assert await asyncio.to_thread(started.wait, 1)
            stop.set()
            await asyncio.wait_for(task, .5)
            assert not release.is_set(), "Stop waited for the blocking engine worker"
            release.set()
            await asyncio.sleep(.05)
            assert actions.manager.active.serial == "emulator-5556"
            assert actions.manager.get("emulator-5554").tier == "1080"
        finally:
            release.set()
            stop.set()
            await asyncio.gather(task, return_exceptions=True)
            await ws.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("error_id,tracked,healthy", [("valid", False, True), ("valid", True, False), ("malformed", False, False)])
async def test_delivery_rejection_is_nonfatal_only_for_untracked_valid_id(error_id, tracked, healthy):
    from remote_protocol import Reply, ErrorPayload, format_reply
    from server.remote_dispatch import RemoteDispatcher
    module = importlib.import_module("server.remote_client")
    client = module.RemoteHostClient("https://service.example", InstallationIdentity(installation_id="host", credential="secret"), RemoteDispatcher(Actions(), PairingStore()))
    client._loop = asyncio.get_running_loop()
    client._wake = asyncio.Event()
    request_id = str(uuid.uuid4()) if error_id == "valid" else "malformed"
    if tracked:
        client._lifecycle_ids.add(request_id)
    raw = format_reply(Reply(v=1, id=request_id, ok=False, error=ErrorPayload(code="invalid_request", message="Delivery rejected")))
    entered = asyncio.Event()
    held_open = asyncio.Event()

    class Connection:
        def __aiter__(self):
            async def frames():
                yield raw
                entered.set()
                await held_open.wait()
            return frames()

    client.connection = Connection()
    running = asyncio.create_task(client._connected())
    try:
        if healthy:
            await asyncio.wait_for(entered.wait(), .5)
            assert not running.done()
        else:
            with pytest.raises(ValueError):
                await asyncio.wait_for(running, .5)
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)
