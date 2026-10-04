"""Admission tests exercise real metadata decisions and real broker sockets."""
import json
import uuid

import pytest
from broker.app import BrokerSettings
from broker.limits import BrokerLimits
from broker.registry import Registry
from tests.test_broker import broker_env, wire, authenticate_host, approve_viewer


def authorization(routing_id, generation=1, session_id=None):
    from remote_protocol import parse_media_authorization
    return parse_media_authorization(wire("media_authorize", {"routing_id": routing_id, "session_id": session_id or str(uuid.uuid4()), "generation": generation}))


@pytest.fixture
def admission(tmp_path):
    from broker.media import MediaAdmission
    now = [1000.0]
    registry = Registry()
    settings = BrokerSettings(storage_path=str(tmp_path / "store"), allowed_origins=[], turn_shared_secret="backend-only", max_active_streams=2, stun_urls=["stun:example:3478"], turn_urls=["turn:example:3478?transport=udp"])
    media = MediaAdmission(settings, BrokerLimits(lambda: now[0]), registry, lambda: now[0])
    return media, registry, now, settings


def owner(registry, installation="a"):
    host = registry.register_host(installation, object())
    viewer = registry.register_viewer(str(uuid.uuid4()), installation, object(), host.epoch)
    viewer.authenticated = True
    viewer.device_id = "device"
    return host, viewer


def pending(registry, viewer, op="select", payload=None, received_at=1000.0):
    return registry.add_pending_request(viewer, str(uuid.uuid4()), op=op, payload=payload or {"serial": "emulator-5554"}, received_at=received_at)


@pytest.mark.asyncio
async def test_admission_requires_live_select_and_current_owner(admission):
    from broker.media import MediaError
    media, registry, now, settings = admission
    host, viewer = owner(registry)
    other, _ = owner(registry, "b")
    preview = pending(registry, viewer, "preview")
    select = pending(registry, viewer)
    for sender, request in ((host, authorization(preview.routing_id)), (other, authorization(select.routing_id))):
        with pytest.raises(MediaError, match="invalid_request"):
            media.authorize(sender, request)
    viewer.authenticated = False
    with pytest.raises(MediaError, match="not_paired"):
        media.authorize(host, authorization(select.routing_id))
    viewer.authenticated = True
    registry.resolve_pending_request(select.routing_id, "a", host.epoch)
    with pytest.raises(MediaError, match="invalid_request"):
        media.authorize(host, authorization(select.routing_id))
    assert not media.admissions


@pytest.mark.asyncio
async def test_separate_endpoint_credentials_capacity_and_revision_fences(admission):
    from broker.media import MediaError
    media, registry, now, settings = admission
    host, viewer = owner(registry)
    request = authorization(pending(registry, viewer).routing_id)
    bundles = media.authorize(host, request)
    assert bundles.host.expires_at == 4600
    assert bundles.viewer.renew_after == 3300
    assert bundles.host.ice_servers[1]["credential"] != bundles.viewer.ice_servers[1]["credential"]
    assert "backend-only" not in bundles.model_dump_json()
    assert "backend-only" not in repr(settings)
    b_host, b_viewer = owner(registry, "b")
    media.authorize(b_host, authorization(pending(registry, b_viewer).routing_id))
    b_slot = media.admissions["b"]
    c_host, c_viewer = owner(registry, "c")
    with pytest.raises(MediaError, match="quota_exceeded"):
        media.authorize(c_host, authorization(pending(registry, c_viewer).routing_id))
    replacement = authorization(pending(registry, viewer).routing_id, 2)
    media.authorize(host, replacement)
    assert len(media.admissions) == 2
    assert media.admissions["b"] is b_slot
    assert not media.release(host, request.payload["session_id"], 1)
    media.cancel(viewer, request.payload["routing_id"])
    assert media.admissions["a"].session_id == replacement.payload["session_id"]
    assert media.release(host, replacement.payload["session_id"], 2)
    assert media.release(host, replacement.payload["session_id"], 2)
    with pytest.raises(MediaError, match="stale_generation"):
        media.authorize(host, authorization(pending(registry, viewer).routing_id, 1))


@pytest.mark.asyncio
async def test_issuance_thirteenth_request_rejected_within_rolling_hour(admission):
    from broker.media import MediaError
    media, registry, now, _ = admission
    host, viewer = owner(registry)
    for generation in range(1, 13):
        media.authorize(host, authorization(pending(registry, viewer, received_at=now[0]).routing_id, generation))
        now[0] += 30
    with pytest.raises(MediaError, match="quota_exceeded"):
        media.authorize(host, authorization(pending(registry, viewer, received_at=now[0]).routing_id, 13))


@pytest.mark.asyncio
async def test_setup_expiry_stun_only_and_original_host_grace(admission):
    from broker.media import MediaError
    media, registry, now, settings = admission
    host, viewer = owner(registry)
    settings.relay_available = False
    first = authorization(pending(registry, viewer).routing_id)
    bundle = media.authorize(host, first)
    assert bundle.viewer.ice_servers == [{"urls": ["stun:example:3478"]}]
    assert bundle.viewer.relay_available is False
    assert bundle.viewer.expires_at == 4600
    media.sweep(1030)
    assert not media.admissions
    now[0] = 1000
    second = authorization(pending(registry, viewer).routing_id, 2)
    media.authorize(host, second)
    media.admissions["a"].established = True
    media.host_disconnected(host)
    registry.remove_host(host)
    now[0] = 1020
    replacement, fresh_viewer = owner(registry)
    with pytest.raises(MediaError, match="busy"):
        media.authorize(replacement, authorization(pending(registry, fresh_viewer).routing_id, 3))
    media.host_disconnected(replacement)
    media.sweep(1060)
    assert not media.admissions


def test_registration_preserves_entire_issuance_allowance(broker_env):
    client, _, _ = broker_env
    identity = client.post("/installations").json()
    limits = client.app.state.limits
    assert all(limits.check_credential_issuance(identity["installation_id"]) for _ in range(12))
    assert not limits.check_credential_issuance(identity["installation_id"])


def authorize_socket(host, routed, generation=1):
    request = authorization(routed["id"], generation)
    host.send_text(request.model_dump_json())
    reply = json.loads(host.receive_text())
    assert reply["ok"], reply
    return request, reply["result"]


def selected_result(request, bundles):
    return {"ok": True, "id": "emulator-5554", "serial": "emulator-5554", "name": "Phone", "w": 1920, "h": 1080, "tier": "1080p", "session_id": request.payload["session_id"], "generation": request.payload["generation"], **bundles["viewer"]}


def test_viewer_disconnect_cancels_completed_session(broker_env):
    client, _, _ = broker_env
    identity = client.post("/installations").json()
    with client.websocket_connect("/connect") as host:
        authenticate_host(host, identity)
        with client.websocket_connect("/connect") as viewer:
            approve_viewer(host, viewer, identity)
            viewer.send_text(wire("select", {"serial": "emulator-5554"}))
            routed = json.loads(host.receive_text())
            request, bundles = authorize_socket(host, routed)
            host.send_json({"v": 1, "id": routed["id"], "ok": True, "result": selected_result(request, bundles)})
            assert viewer.receive_json()["ok"]
        cancel = host.receive_json()
        assert cancel["op"] == "media_cancel"
        assert cancel["payload"] == {"installation_id": identity["installation_id"], "host_epoch": routed["context"]["host_epoch"], "viewer_id": routed["context"]["viewer_id"]}
        assert not client.app.state.media.admissions


def test_timeout_after_host_reply_sends_media_cancel(broker_env, monkeypatch):
    monkeypatch.setattr("broker.app.COMMAND_TIMEOUT_SECONDS", .1)
    client, _, _ = broker_env
    identity = client.post("/installations").json()
    with client.websocket_connect("/connect") as host:
        authenticate_host(host, identity)
        with client.websocket_connect("/connect") as viewer:
            approve_viewer(host, viewer, identity)
            viewer.send_text(wire("select", {"serial": "emulator-5554"}))
            routed = host.receive_json()
            request, bundles = authorize_socket(host, routed)
            conn = next(iter(client.app.state.registry.viewers.values()))
            client.portal.call(conn.send_lock.acquire)
            host.send_json({"v": 1, "id": routed["id"], "ok": True, "result": selected_result(request, bundles)})
            cancel = host.receive_json()
            assert cancel["op"] == "media_cancel"
            assert cancel["payload"]["routing_id"] == routed["id"]
            assert not client.app.state.media.admissions
            client.portal.call(conn.send_lock.release)


@pytest.mark.asyncio
async def test_current_host_can_release_exact_predecessor_but_old_socket_cannot(admission):
    from broker.media import MediaError
    media, registry, now, _ = admission
    host, viewer = owner(registry)
    old = authorization(pending(registry, viewer).routing_id)
    media.authorize(host, old)
    media.admissions["a"].established = True
    media.host_disconnected(host)
    fresh, phone = owner(registry)
    assert not media.release(host, old.payload["session_id"], 1)
    assert media.release(fresh, old.payload["session_id"], 1)
    successor = authorization(pending(registry, phone).routing_id, 2)
    media.authorize(fresh, successor)
    assert not media.release(fresh, old.payload["session_id"], 1)
    media.cancel(viewer)
    assert media.admissions["a"].session_id == successor.payload["session_id"]


@pytest.mark.asyncio
async def test_viewer_disconnect_cannot_free_disconnected_host_grace(admission):
    media, registry, now, _ = admission
    host, viewer = owner(registry)
    media.authorize(host, authorization(pending(registry, viewer).routing_id))
    media.admissions["a"].established = True
    media.host_disconnected(host)
    registry.remove_host(host)
    media.cancel(viewer)
    assert "a" in media.admissions
    media.sweep(1059.9)
    assert "a" in media.admissions
    media.sweep(1060)
    assert not media.admissions


@pytest.mark.asyncio
async def test_renew_revalidates_exact_viewer_and_accepts_released_predecessor(admission):
    from broker.media import MediaError
    media, registry, now, _ = admission
    host, viewer = owner(registry)
    old = authorization(pending(registry, viewer).routing_id)
    media.authorize(host, old)
    payload = {key: old.payload[key] for key in ("session_id", "generation")}
    foreign = registry.register_viewer("other-phone", "a", object(), host.epoch)
    foreign.authenticated, foreign.device_id = True, "device"
    with pytest.raises(MediaError, match="stale_generation"):
        media.authorize(host, authorization(pending(registry, foreign, "renew", payload).routing_id, 2))
    assert media.release(host, payload["session_id"], 1)
    request = authorization(pending(registry, viewer, "renew", payload).routing_id, 2)
    media.authorize(host, request)
    assert len(media.admissions) == 1
    with pytest.raises(MediaError, match="invalid_request"):
        media.authorize(host, request)


def select_socket(host, viewer, generation=1):
    viewer.send_text(wire("select", {"serial": "emulator-5554"}))
    routed = host.receive_json()
    request, bundles = authorize_socket(host, routed, generation)
    result = selected_result(request, bundles)
    host.send_json({"v": 1, "id": routed["id"], "ok": True, "result": result})
    assert viewer.receive_json()["result"] == result
    return request, routed


def test_close_reply_after_private_release_is_successful(broker_env):
    client, _, _ = broker_env
    identity = client.post("/installations").json()
    with client.websocket_connect("/connect") as host, client.websocket_connect("/connect") as viewer:
        authenticate_host(host, identity)
        approve_viewer(host, viewer, identity)
        request, _ = select_socket(host, viewer)
        payload = {key: request.payload[key] for key in ("session_id", "generation")}
        viewer.send_text(wire("close", payload))
        routed = host.receive_json()
        host.send_text(wire("media_release", payload))
        assert host.receive_json()["result"] == {"released": True}
        host.send_json({"v": 1, "id": routed["id"], "ok": True, "result": {"closed": True}})
        reply = viewer.receive_json()
        assert reply["ok"], reply
        assert reply["result"] == {"closed": True}
        assert not client.app.state.media.admissions


def test_two_installations_quota_failure_preserves_other_stream(broker_env):
    client, _, _ = broker_env
    a, b = client.post("/installations").json(), client.post("/installations").json()
    with client.websocket_connect("/connect") as ha, client.websocket_connect("/connect") as hb, client.websocket_connect("/connect") as va, client.websocket_connect("/connect") as vb:
        authenticate_host(ha, a)
        authenticate_host(hb, b)
        approve_viewer(ha, va, a)
        approve_viewer(hb, vb, b)
        b_request, _ = select_socket(hb, vb)
        for _ in range(12):
            assert client.app.state.limits.check_credential_issuance(a["installation_id"])
        va.send_text(wire("select", {"serial": "emulator-5554"}))
        routed = ha.receive_json()
        request = authorization(routed["id"])
        ha.send_text(request.model_dump_json())
        assert ha.receive_json()["error"]["code"] == "quota_exceeded"
        ha.send_json({"v": 1, "id": routed["id"], "ok": False, "error": {"code": "quota_exceeded", "message": "No relay allowance"}})
        assert va.receive_json()["error"]["code"] == "quota_exceeded"
        assert client.app.state.media.admissions[b["installation_id"]].session_id == b_request.payload["session_id"]
        vb.send_text(wire("instances"))
        routed_b = hb.receive_json()
        hb.send_json({"v": 1, "id": routed_b["id"], "ok": True, "result": {"instances": []}})
        assert vb.receive_json()["result"] == {"instances": []}


def test_socket_authorization_rejects_foreign_preview_completed_and_viewer_role(broker_env):
    client, _, _ = broker_env
    a, b = client.post("/installations").json(), client.post("/installations").json()
    with client.websocket_connect("/connect") as ha, client.websocket_connect("/connect") as hb, client.websocket_connect("/connect") as viewer:
        authenticate_host(ha, a)
        authenticate_host(hb, b)
        approve_viewer(ha, viewer, a)
        viewer.send_text(wire("preview", {"serial": "emulator-5554"}))
        preview = ha.receive_json()
        ha.send_text(authorization(preview["id"]).model_dump_json())
        assert ha.receive_json()["error"]["code"] == "invalid_request"
        ha.send_json({"v": 1, "id": preview["id"], "ok": True, "result": {}})
        assert viewer.receive_json()["ok"]
        viewer.send_text(wire("select", {"serial": "emulator-5554"}))
        select = ha.receive_json()
        request = authorization(select["id"])
        hb.send_text(request.model_dump_json())
        assert hb.receive_json()["error"]["code"] == "invalid_request"
        viewer.send_text(request.model_dump_json())
        assert viewer.receive_json()["error"]["code"] == "invalid_request"
        ha.send_text(request.model_dump_json())
        bundles = ha.receive_json()["result"]
        ha.send_json({"v": 1, "id": select["id"], "ok": True, "result": selected_result(request, bundles)})
        assert viewer.receive_json()["ok"]
        ha.send_text(authorization(select["id"], 2).model_dump_json())
        assert ha.receive_json()["error"]["code"] == "invalid_request"


def test_successful_negotiate_survives_setup_but_logical_expiry_retires(broker_env):
    client, _, _ = broker_env
    identity = client.post("/installations").json()
    with client.websocket_connect("/connect") as host, client.websocket_connect("/connect") as viewer:
        authenticate_host(host, identity)
        approve_viewer(host, viewer, identity)
        request, _ = select_socket(host, viewer)
        payload = {key: request.payload[key] for key in ("session_id", "generation")}
        viewer.send_text(wire("negotiate", {**payload, "offer": "v=0", "timeout_ms": 30000}))
        negotiate = host.receive_json()
        host.send_json({"v": 1, "id": negotiate["id"], "ok": True, "result": {**payload, "answer": "v=0"}})
        assert viewer.receive_json()["result"] == {**payload, "answer": "v=0"}
        media = client.app.state.media
        slot = media.admissions[identity["installation_id"]]
        client.portal.call(media.sweep, slot.setup_deadline)
        assert slot.established
        assert identity["installation_id"] in media.admissions
        client.portal.call(media.sweep, slot.expires_at)
        assert identity["installation_id"] not in media.admissions


@pytest.mark.parametrize("capacity", [None, 0, -1, True, "2"])
def test_production_capacity_must_be_explicit_positive_integer(tmp_path, capacity):
    values = dict(storage_path=str(tmp_path / "store"), allowed_origins=[], turn_shared_secret="secret")
    if capacity is not None:
        values["max_active_streams"] = capacity
    with pytest.raises(ValueError):
        BrokerSettings(**values)


def test_failed_viewer_delivery_cancels_admitted_session(broker_env, monkeypatch):
    client, _, _ = broker_env
    identity = client.post("/installations").json()
    with client.websocket_connect("/connect") as host, client.websocket_connect("/connect") as viewer:
        authenticate_host(host, identity)
        approve_viewer(host, viewer, identity)
        viewer.send_text(wire("select", {"serial": "emulator-5554"}))
        routed = host.receive_json()
        request, bundles = authorize_socket(host, routed)
        conn = next(iter(client.app.state.registry.viewers.values()))
        original = conn.websocket.send_text
        async def fail_success(data):
            if json.loads(data).get("ok"):
                raise OSError("disconnected transport")
            return await original(data)
        monkeypatch.setattr(conn.websocket, "send_text", fail_success)
        host.send_json({"v": 1, "id": routed["id"], "ok": True, "result": selected_result(request, bundles)})
        cancel = host.receive_json()
        assert cancel["op"] == "media_cancel"
        assert cancel["payload"]["routing_id"] == routed["id"]
        assert not client.app.state.media.admissions


def test_reply_waiting_for_viewer_lock_rechecks_replaced_admission(broker_env):
    client, _, _ = broker_env
    identity = client.post("/installations").json()
    with client.websocket_connect("/connect") as host, client.websocket_connect("/connect") as viewer:
        authenticate_host(host, identity)
        approve_viewer(host, viewer, identity)
        viewer.send_text(wire("select", {"serial": "emulator-5554"}))
        first = host.receive_json()
        request, bundles = authorize_socket(host, first)
        conn = next(iter(client.app.state.registry.viewers.values()))
        client.portal.call(conn.send_lock.acquire)
        host.send_json({"v": 1, "id": first["id"], "ok": True, "result": selected_result(request, bundles)})
        viewer.send_text(wire("select", {"serial": "emulator-5554"}))
        second = host.receive_json()
        successor, second_bundles = authorize_socket(host, second, 2)
        client.portal.call(conn.send_lock.release)
        reply = viewer.receive_json()
        assert not reply["ok"]
        assert reply["error"]["code"] == "canceled"
        cancel = host.receive_json()
        assert cancel["payload"]["routing_id"] == first["id"]
        assert client.app.state.media.admissions[identity["installation_id"]].session_id == successor.payload["session_id"]
        host.send_json({"v": 1, "id": second["id"], "ok": True, "result": selected_result(successor, second_bundles)})
        assert viewer.receive_json()["ok"]


def test_answer_timeout_retires_adopted_peer_and_does_not_establish(broker_env):
    client, _, _ = broker_env
    identity = client.post("/installations").json()
    with client.websocket_connect("/connect") as host, client.websocket_connect("/connect") as viewer:
        authenticate_host(host, identity)
        approve_viewer(host, viewer, identity)
        request, _ = select_socket(host, viewer)
        payload = {key: request.payload[key] for key in ("session_id", "generation")}
        viewer.send_text(wire("negotiate", {**payload, "offer": "v=0", "timeout_ms": 100}))
        routed = host.receive_json()
        conn = next(iter(client.app.state.registry.viewers.values()))
        client.portal.call(conn.send_lock.acquire)
        host.send_json({"v": 1, "id": routed["id"], "ok": True, "result": {**payload, "answer": "v=0"}})
        cancel = host.receive_json()
        assert cancel["payload"]["routing_id"] == routed["id"]
        assert not client.app.state.media.admissions
        client.portal.call(conn.send_lock.release)
        assert viewer.receive_json()["error"]["code"] == "timeout"


@pytest.mark.asyncio
async def test_new_host_epoch_can_restart_revision_only_after_predecessor_fence(admission):
    from broker.media import MediaError
    media, registry, now, _ = admission
    host, viewer = owner(registry)
    old = authorization(pending(registry, viewer).routing_id, 9)
    media.authorize(host, old)
    media.admissions["a"].established = True
    media.host_disconnected(host)
    replacement, fresh = owner(registry)
    request = authorization(pending(registry, fresh).routing_id, 1)
    with pytest.raises(MediaError, match="busy"):
        media.authorize(replacement, request)
    assert media.release(replacement, old.payload["session_id"], 9)
    media.authorize(replacement, request)
    assert media.admissions["a"].generation == 1
    assert not media.release(host, old.payload["session_id"], 9)
    with pytest.raises(MediaError, match="stale_generation"):
        media.authorize(replacement, authorization(pending(registry, fresh).routing_id, 1))


@pytest.mark.asyncio
async def test_broker_restart_drops_transient_admission_and_revision(admission):
    from broker.media import MediaAdmission
    media, registry, now, settings = admission
    host, viewer = owner(registry)
    media.authorize(host, authorization(pending(registry, viewer).routing_id, 100))
    restarted_registry = Registry()
    restarted = MediaAdmission(settings, BrokerLimits(lambda: now[0]), restarted_registry, lambda: now[0])
    assert not restarted.admissions
    current, fresh = owner(restarted_registry)
    restarted.authorize(current, authorization(pending(restarted_registry, fresh).routing_id, 1))
    assert restarted.admissions["a"].generation == 1


def test_answered_undelivered_request_keeps_duplicate_id_reserved(broker_env):
    client, _, _ = broker_env
    identity = client.post("/installations").json()
    with client.websocket_connect("/connect") as host, client.websocket_connect("/connect") as viewer:
        authenticate_host(host, identity)
        approve_viewer(host, viewer, identity)
        request_id = str(uuid.uuid4())
        viewer.send_text(wire("select", {"serial": "emulator-5554"}, request_id))
        routed = host.receive_json()
        request, bundles = authorize_socket(host, routed)
        conn = next(iter(client.app.state.registry.viewers.values()))
        client.portal.call(conn.send_lock.acquire)
        host.send_json({"v": 1, "id": routed["id"], "ok": True, "result": selected_result(request, bundles)})
        # A host round trip proves its reply was processed before the assertion.
        host.send_text(wire("pairing_close", {"handle": "x" * 43}))
        assert host.receive_json()["ok"]
        assert request_id in conn.pending_requests
        client.portal.call(conn.send_lock.release)
        assert viewer.receive_json()["ok"]


def test_failed_cancel_delivery_retains_capacity_until_original_host_grace(broker_env, monkeypatch):
    client, _, _ = broker_env
    identity = client.post("/installations").json()
    with client.websocket_connect("/connect") as host:
        authenticate_host(host, identity)
        with client.websocket_connect("/connect") as viewer:
            approve_viewer(host, viewer, identity)
            request, _ = select_socket(host, viewer)
            conn = client.app.state.registry.get_host(identity["installation_id"])
            original = conn.websocket.send_text
            async def fail_cancel(data):
                if json.loads(data).get("op") == "media_cancel":
                    raise OSError("disconnected host transport")
                return await original(data)
            monkeypatch.setattr(conn.websocket, "send_text", fail_cancel)
        media = client.app.state.media
        assert identity["installation_id"] in media.admissions
        slot = media.admissions[identity["installation_id"]]
        assert slot.grace_deadline is not None
        with client.websocket_connect("/connect") as replacement, client.websocket_connect("/connect") as fresh:
            authenticate_host(replacement, identity)
            approve_viewer(replacement, fresh, identity)
            fresh.send_text(wire("select", {"serial": "emulator-5554"}))
            routed = replacement.receive_json()
            replacement.send_text(authorization(routed["id"], 2).model_dump_json())
            assert replacement.receive_json()["error"]["code"] == "busy"


def test_host_outbound_queue_is_bounded_without_closing_other_viewer(broker_env, monkeypatch):
    import asyncio
    monkeypatch.setattr("broker.app.COMMAND_TIMEOUT_SECONDS", .3)
    client, _, _ = broker_env
    identity = client.post("/installations").json()
    with client.websocket_connect("/connect") as host, client.websocket_connect("/connect") as a, client.websocket_connect("/connect") as b:
        authenticate_host(host, identity)
        approve_viewer(host, a, identity)
        approve_viewer(host, b, identity)
        conn = client.app.state.registry.get_host(identity["installation_id"])
        client.portal.call(conn.send_lock.acquire)
        for _ in range(32):
            a.send_text(wire("instances"))
        async def wait_for_pending():
            async with asyncio.timeout(.2):
                while len(client.app.state.registry.pending_requests) < 32:
                    await asyncio.sleep(.001)
        client.portal.call(wait_for_pending)
        b.send_text(wire("instances"))
        assert b.receive_json()["error"]["code"] == "busy"
        client.portal.call(conn.send_lock.release)
        for _ in range(32):
            routed = host.receive_json()
            host.send_json({"v": 1, "id": routed["id"], "ok": True, "result": {}})
        assert all(a.receive_json()["ok"] for _ in range(32))
        b.send_text(wire("instances"))
        routed = host.receive_json()
        host.send_json({"v": 1, "id": routed["id"], "ok": True, "result": {}})
        assert b.receive_json()["ok"]


@pytest.mark.asyncio
async def test_answered_undelivered_work_counts_toward_installation_limit():
    registry = Registry()
    host = registry.register_host("a", object())
    for index in range(8):
        viewer = registry.register_viewer(str(index), "a", object(), host.epoch)
        for request in range(32):
            item = registry.add_pending_request(viewer, str(request))
            assert item is not None
            registry.resolve_pending_request(item.routing_id, "a", host.epoch)
    extra = registry.register_viewer("extra", "a", object(), host.epoch)
    assert registry.add_pending_request(extra, "overflow") is None


@pytest.mark.asyncio
async def test_parallel_negotiate_cannot_replace_the_cancel_correlation(admission):
    from broker.media import MediaError
    media, registry, now, _ = admission
    host, viewer = owner(registry)
    selection = authorization(pending(registry, viewer).routing_id)
    media.authorize(host, selection)
    payload = {key: selection.payload[key] for key in ("session_id", "generation")}
    first = pending(registry, viewer, "negotiate", payload)
    media.track_request(first)
    second = pending(registry, viewer, "negotiate", payload)
    with pytest.raises(MediaError, match="busy"):
        media.track_request(second)
    media.cancel_request(second)
    assert not media.admissions["a"].canceled
    media.cancel_request(first)
    assert media.admissions["a"].canceled


def test_control_only_disconnect_preserves_another_viewers_media(broker_env):
    client, _, _ = broker_env
    identity = client.post("/installations").json()
    with client.websocket_connect("/connect") as host, client.websocket_connect("/connect") as streaming:
        authenticate_host(host, identity)
        approve_viewer(host, streaming, identity)
        request, _ = select_socket(host, streaming)
        with client.websocket_connect("/connect") as control:
            approve_viewer(host, control, identity)
        # This round trip also exposes any unsolicited cancellation queued first.
        marker = str(uuid.uuid4())
        host.send_text(wire("pairing_close", {"handle": "x" * 43}, marker))
        assert host.receive_json()["id"] == marker
        assert client.app.state.media.admissions[identity["installation_id"]].session_id == request.payload["session_id"]


@pytest.mark.asyncio
async def test_nonlatest_session_uuid_cannot_be_reused(admission):
    from broker.media import MediaError
    media, registry, _, _ = admission
    host, viewer = owner(registry)
    first = authorization(pending(registry, viewer).routing_id)
    media.authorize(host, first)
    successor = authorization(pending(registry, viewer).routing_id, 2)
    media.authorize(host, successor)
    with pytest.raises(MediaError, match="stale_generation"):
        media.authorize(host, authorization(pending(registry, viewer).routing_id, 3, first.payload["session_id"]))
    assert media.admissions["a"].session_id == successor.payload["session_id"]


@pytest.mark.asyncio
async def test_session_history_fails_closed_at_bound_without_evicting(admission):
    from broker.media import MediaError
    media, registry, now, _ = admission
    host, viewer = owner(registry)
    first = None
    for generation in range(1, 4097):
        request = authorization(pending(registry, viewer, received_at=now[0]).routing_id, generation)
        media.authorize(host, request)
        registry.drop_pending(registry.pending_requests[request.payload["routing_id"]])
        if first is None:
            first = request
        if generation < 4096:
            now[0] += 3600
    successor = media.admissions["a"]
    with pytest.raises(MediaError, match="quota_exceeded"):
        media.authorize(host, authorization(pending(registry, viewer, received_at=now[0]).routing_id, 4097))
    with pytest.raises(MediaError, match="stale_generation"):
        media.authorize(host, authorization(pending(registry, viewer, received_at=now[0]).routing_id, 4097, first.payload["session_id"]))
    assert media.admissions["a"] is successor
    media.host_disconnected(host)
    current, phone = owner(registry)
    assert media.release(current, successor.session_id, successor.generation)
    fresh = authorization(pending(registry, phone, received_at=now[0]).routing_id, 1)
    media.authorize(current, fresh)
    assert media.admissions["a"].session_id == fresh.payload["session_id"]


@pytest.mark.asyncio
async def test_new_epoch_resets_bounded_history_after_exact_predecessor_release(admission):
    from broker.media import MediaError
    media, registry, now, _ = admission
    host, viewer = owner(registry)
    old = authorization(pending(registry, viewer).routing_id, 7)
    media.authorize(host, old)
    media.admissions["a"].established = True
    media.host_disconnected(host)
    current, phone = owner(registry)
    with pytest.raises(MediaError, match="stale_generation"):
        media.authorize(current, authorization(pending(registry, phone).routing_id, 1, old.payload["session_id"]))
    with pytest.raises(MediaError, match="busy"):
        media.authorize(current, authorization(pending(registry, phone).routing_id, 1))
    assert media.release(current, old.payload["session_id"], 7)
    fresh = authorization(pending(registry, phone).routing_id, 1)
    media.authorize(current, fresh)
    assert media.admissions["a"].session_id == fresh.payload["session_id"]
    assert media.admissions["a"].generation == 1


@pytest.mark.asyncio
async def test_new_host_releases_predecessor_before_revision_restart(admission):
    from broker.media import MediaError

    media, registry, now, _ = admission
    old_host, old_viewer = owner(registry)
    old = authorization(pending(registry, old_viewer).routing_id, generation=7)
    media.authorize(old_host, old)
    media.admissions["a"].established = True
    media.host_disconnected(old_host)
    registry.remove_host(old_host)
    now[0] += 10
    new_host, new_viewer = owner(registry)
    fresh = authorization(pending(registry, new_viewer).routing_id, generation=1)

    with pytest.raises(MediaError, match="busy"):
        media.authorize(new_host, fresh)
    assert not media.release(old_host, old.payload["session_id"], 7)
    # The current PC reports exact predecessor cleanup, then requests a new session.
    assert media.release(new_host, old.payload["session_id"], 7)
    bundles = media.authorize(new_host, fresh)
    assert fresh.payload["session_id"] != old.payload["session_id"]
    assert media.admissions["a"].generation == 1
    assert bundles.viewer.renew_after == 3300
    assert not media.release(new_host, old.payload["session_id"], 7)
    assert media.admissions["a"].session_id == fresh.payload["session_id"]


@pytest.mark.asyncio
async def test_endpoint_credentials_are_distinct_and_secret_stays_private(admission):
    import base64
    import hashlib
    import hmac

    media, registry, now, settings = admission
    host, viewer = owner(registry)
    request = authorization(pending(registry, viewer).routing_id)
    bundles = media.authorize(host, request)
    for endpoint in ("host", "viewer"):
        bundle = getattr(bundles, endpoint)
        turn = bundle.ice_servers[1]
        expected_username = (
            f"{int(now[0]) + 3600}:a:{request.payload['session_id']}:{endpoint}"
        )
        expected_credential = base64.b64encode(hmac.new(
            settings.turn_shared_secret.encode(), expected_username.encode(), hashlib.sha1
        ).digest()).decode("ascii")
        assert turn["username"] == expected_username
        assert turn["credential"] == expected_credential
        assert bundle.expires_at == int(now[0]) + 3600
    assert bundles.host.ice_servers[1] != bundles.viewer.ice_servers[1]
    assert settings.turn_shared_secret not in bundles.model_dump_json()


@pytest.mark.asyncio
async def test_current_host_release_of_never_admitted_session_is_idempotent_and_successor_safe(admission):
    media, registry, _, _ = admission
    old, _ = owner(registry)
    current, viewer = owner(registry)
    unknown_session = str(uuid.uuid4())
    assert not media.release(old, unknown_session, 1)
    assert media.release(current, unknown_session, 1)
    assert media.release(current, unknown_session, 1)
    live = authorization(pending(registry, viewer).routing_id, 2)
    media.authorize(current, live)
    assert not media.release(current, unknown_session, 1)
    assert not media.release(old, live.payload["session_id"], 2)
    assert media.admissions["a"].session_id == live.payload["session_id"]
    assert media.release(current, live.payload["session_id"], 2)
    assert media.release(current, unknown_session, 1)
