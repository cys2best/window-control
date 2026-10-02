import asyncio
import base64
import importlib
import uuid
from types import SimpleNamespace

import pytest
from fastapi.responses import Response
from remote_protocol import Command, MAX_PREVIEW_BYTES
from server.pairing import PairingStore


class Actions:
    def __init__(self):
        self.calls = []
        self.image = b"jpeg"

    async def instances(self):
        self.calls.append("instances")
        return [{"serial": "emulator-5554"}]

    async def select(self, serial, advertised_host):
        self.calls.append((serial, advertised_host))
        return {"serial": serial, "generation": 2, "whep_url": "http://private", "whep_token": "secret", "ice_servers": []}

    async def preview(self, serial):
        self.calls.append("preview")
        return Response(self.image, media_type="image/jpeg")


def setup_dispatch():
    module = importlib.import_module("server.remote_dispatch")
    pairing = PairingStore()
    code = pairing.start_pairing()
    token = pairing.pair(code, "Phone")
    actions = Actions()
    return module.RemoteDispatcher(actions, pairing), actions, pairing, token


def cmd(op, payload=None):
    return {"v": 1, "id": str(uuid.uuid4()), "op": op, "payload": payload or {}}


def test_loopback_bridge_still_requires_device_token():
    dispatch, actions, pairing, token = setup_dispatch()
    command = Command(**cmd("instances"))
    reply = asyncio.run(dispatch.dispatch(command, "bad"))
    assert reply.error.code == "not_paired"
    assert actions.calls == []
    assert asyncio.run(dispatch.dispatch(command, token)).ok
    pairing.remove_all()
    assert asyncio.run(dispatch.dispatch(command, token)).error.code == "not_paired"
    assert actions.calls == ["instances"]


@pytest.mark.parametrize("op,payload", [("forward", {}), ("select", {"serial": "emulator-5554", "url": "http://127.0.0.1/admin"}), ("instances", {"context": {"token": "bad"}})])
def test_unknown_operation_and_url_rejected(op, payload):
    dispatch, actions, pairing, token = setup_dispatch()
    from remote_protocol import parse_frame
    import json
    with pytest.raises(ValueError):
        parse_frame(json.dumps(cmd(op, payload)))
    assert actions.calls == []


@pytest.mark.parametrize("op", ["generate_code", "revoke_device", "pairing_open", "host_auth"])
def test_viewer_cannot_generate_code_or_revoke_device(op):
    dispatch, actions, pairing, token = setup_dispatch()
    # Even a caller constructing Command without validation cannot elevate roles.
    command = Command.model_construct(**cmd(op))
    assert asyncio.run(dispatch.dispatch(command, token)).error.code == "invalid_request"
    assert actions.calls == []


def test_remote_select_does_not_publish_engine_capabilities():
    dispatch, actions, pairing, token = setup_dispatch()
    reply = asyncio.run(dispatch.dispatch(Command(**cmd("select", {"serial": "emulator-5554"})), token))
    assert reply.ok and reply.result == {"serial": "emulator-5554", "generation": 2}
    assert actions.calls == [("emulator-5554", "127.0.0.1")]


def test_remote_preview_respects_size_limit():
    dispatch, actions, pairing, token = setup_dispatch()
    command = Command(**cmd("preview", {"serial": "emulator-5554"}))
    reply = asyncio.run(dispatch.dispatch(command, token))
    assert reply.result == {"mime": "image/jpeg", "data_base64": base64.b64encode(b"jpeg").decode()}
    actions.image = b"x" * (MAX_PREVIEW_BYTES + 1)
    assert asyncio.run(dispatch.dispatch(command, token)).error.code == "unavailable"


def test_media_operations_fail_explicitly_until_coordinator_injected():
    dispatch, actions, pairing, token = setup_dispatch()
    command = Command(**cmd("renew", {"session_id": str(uuid.uuid4()), "generation": 1}))
    assert asyncio.run(dispatch.dispatch(command, token)).error.code == "unavailable"
