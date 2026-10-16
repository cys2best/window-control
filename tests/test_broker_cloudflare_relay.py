import itertools
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from broker.app import BrokerSettings, create_broker_app
from tests.test_broker import approve_viewer, authenticate_host, wire
from tests.test_broker_license import activate, register, turn_servers

CLOUDFLARE_STUN = "stun:stun.cloudflare.com:3478"


def cloudflare_settings(tmp_path, **overrides):
    values = dict(
        storage_path=str(tmp_path / "installations.json"),
        allowed_origins=["https://control.example.com"],
        max_active_streams=2,
        stun_urls=[CLOUDFLARE_STUN],
        relay_provider="cloudflare",
        cloudflare_turn_key_id="key0123456789abcdef",
        cloudflare_turn_api_token="token-value-0123456789",
    )
    values.update(overrides)
    return BrokerSettings(**values)


class FakeCloudflare:
    def __init__(self, respond=None):
        self.calls = 0
        self.counter = itertools.count(1)
        self.respond = respond

    def __call__(self, request):
        self.calls += 1
        if self.respond is not None:
            return self.respond(request)
        n = next(self.counter)
        return httpx.Response(201, json={"iceServers": [
            {"urls": ["stun:stun.cloudflare.com:3478"]},
            {"urls": ["turn:turn.cloudflare.com:3478?transport=udp",
                      "turns:turn.cloudflare.com:443?transport=tcp"],
             "username": f"cf-user-{n}", "credential": f"cf-credential-{n}"},
        ]})


@pytest.fixture
def cloudflare_env(tmp_path):
    def start(fake):
        app = create_broker_app(cloudflare_settings(tmp_path),
                                turn_client=httpx.AsyncClient(transport=httpx.MockTransport(fake)))
        return TestClient(app)
    return start


def licensed_select(client, licensed=True):
    identity = register(client)
    key = client.app.state.licenses.issue(now=0)
    if licensed:
        assert activate(client, identity, key).status_code == 200
    with client.websocket_connect("/connect") as host, client.websocket_connect("/connect") as viewer:
        authenticate_host(host, identity)
        approve_viewer(host, viewer, identity)
        return select_bundle_both(host, viewer), key


def select_bundle_both(host, viewer):
    viewer.send_text(wire("select", {"serial": "emulator-5554"}))
    routed = host.receive_json()
    host.send_text(wire("media_authorize", {"routing_id": routed["id"], "session_id": str(uuid.uuid4()),
                                            "generation": 1}))
    reply = host.receive_json()
    assert reply["ok"], reply
    host.send_json({"v": 1, "id": routed["id"], "ok": False,
                    "error": {"code": "busy", "message": "test ends here"}})
    viewer.receive_json()
    assert host.receive_json()["op"] == "media_cancel"
    return reply["result"]


def test_licensed_cloudflare_select_returns_distinct_cloudflare_credentials(cloudflare_env):
    fake = FakeCloudflare()
    with cloudflare_env(fake) as client:
        bundles, _ = licensed_select(client)
    assert fake.calls == 2
    host, viewer = bundles["host"], bundles["viewer"]
    assert viewer["relay_available"] is True and host["relay_available"] is True
    assert {"urls": [CLOUDFLARE_STUN]} in viewer["ice_servers"]
    assert turn_servers(viewer)[0]["credential"] != turn_servers(host)[0]["credential"]


def test_unlicensed_cloudflare_select_never_calls_cloudflare(cloudflare_env):
    fake = FakeCloudflare()
    with cloudflare_env(fake) as client:
        bundles, _ = licensed_select(client, licensed=False)
    assert fake.calls == 0
    assert bundles["viewer"]["relay_available"] is False
    assert turn_servers(bundles["viewer"]) == []


def raise_timeout(request):
    raise httpx.ConnectTimeout("slow", request=request)


@pytest.mark.parametrize("respond", [raise_timeout, lambda request: httpx.Response(503)])
def test_cloudflare_failure_falls_back_to_stun_only(cloudflare_env, respond):
    fake = FakeCloudflare(respond)
    with cloudflare_env(fake) as client:
        bundles, _ = licensed_select(client)
    assert fake.calls >= 1
    for endpoint in ("host", "viewer"):
        assert bundles[endpoint]["relay_available"] is False
        assert bundles[endpoint]["ice_servers"] == [{"urls": [CLOUDFLARE_STUN]}]


def test_revocation_during_fetch_discards_prefetched_credentials(cloudflare_env):
    holder = {}
    fake = FakeCloudflare()
    good = fake.__call__

    class Revoking(FakeCloudflare):
        def __call__(self, request):
            holder["client"].app.state.licenses.revoke(holder["key"])
            return good(request)

    revoking = Revoking()
    with cloudflare_env(revoking) as client:
        holder["client"] = client
        identity = register(client)
        holder["key"] = key = client.app.state.licenses.issue(now=0)
        assert activate(client, identity, key).status_code == 200
        with client.websocket_connect("/connect") as host, client.websocket_connect("/connect") as viewer:
            authenticate_host(host, identity)
            approve_viewer(host, viewer, identity)
            bundles = select_bundle_both(host, viewer)
    assert fake.calls >= 1
    assert bundles["viewer"]["relay_available"] is False
    assert turn_servers(bundles["viewer"]) == [] and turn_servers(bundles["host"]) == []


def test_cloudflare_settings_require_key_and_token_and_hide_them(tmp_path):
    for missing in ("cloudflare_turn_key_id", "cloudflare_turn_api_token"):
        with pytest.raises(ValidationError):
            cloudflare_settings(tmp_path, **{missing: None})
    settings = cloudflare_settings(tmp_path)
    assert "key0123456789abcdef" not in repr(settings)
    assert "token-value-0123456789" not in repr(settings)
    with pytest.raises(ValidationError):
        BrokerSettings(storage_path=str(tmp_path / "s"), allowed_origins=["https://control.example.com"],
                       max_active_streams=1)


def test_invalid_media_authorize_never_calls_cloudflare(cloudflare_env):
    fake = FakeCloudflare()
    with cloudflare_env(fake) as client:
        identity = register(client)
        assert activate(client, identity, client.app.state.licenses.issue(now=0)).status_code == 200
        with client.websocket_connect("/connect") as host, client.websocket_connect("/connect") as viewer:
            authenticate_host(host, identity)
            approve_viewer(host, viewer, identity)
            for _ in range(3):
                host.send_text(wire("media_authorize", {"routing_id": str(uuid.uuid4()),
                                                        "session_id": str(uuid.uuid4()), "generation": 1}))
                reply = host.receive_json()
                assert reply["ok"] is False and reply["error"]["code"] == "invalid_request"
    assert fake.calls == 0
