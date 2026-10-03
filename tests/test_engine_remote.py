import importlib

from tests.test_engine_runtime import make_runtime


def test_remote_endpoint_is_private_and_mints_fresh_capability_for_cleanup():
    runtime, state = make_runtime()
    assert runtime.remote_endpoint() is None
    runtime.start()
    first = runtime.remote_endpoint()
    second = runtime.remote_endpoint()
    assert first.admin_port == 51001
    assert first.generation == 0
    assert first.owner is state.engine_state.instances[0]
    assert second.owner is first.owner
    assert repr(first.owner) not in repr(first)
    assert first.capability != second.capability
    assert first.capability not in repr(first)
    runtime.set_tier("1080")
    assert runtime.remote_endpoint().generation == 1
    runtime.stop()
    assert runtime.remote_endpoint() is None

import asyncio
import re
import json
import httpx
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["lost_response", "canceled", "wrong_generation", "oversized"])
async def test_known_attempt_is_closed_when_answer_cannot_be_adopted(failure):
    module = importlib.import_module("server.engine_remote")
    live = set()
    calls = []
    attempts = []
    async def handle(request):
        calls.append(request)
        assert request.url.host == "127.0.0.1"
        assert request.headers["authorization"] == "Bearer fresh"
        if request.method == "DELETE":
            live.discard(request.url.path.rsplit("/", 1)[1])
            return httpx.Response(204)
        body = json.loads(request.content)
        assert 0 < body["timeout_ms"] <= 500
        assert body["ice_servers"][0]["credential"] == "temporary"
        assert re.fullmatch("[a-f0-9]{32}", body["peer_id"])
        assert attempts == [body["peer_id"]]
        live.add(body["peer_id"])
        if failure == "lost_response":
            raise httpx.ReadError("response lost")
        if failure == "canceled":
            raise asyncio.CancelledError
        return httpx.Response(201, json={"peer_id": body["peer_id"], "generation": 8 if failure == "wrong_generation" else 0,
                                         "answer": "x" * (128 * 1024 + 1) if failure == "oversized" else "v=0"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        engine = module.EngineRemoteClient(http_client=http)
        with pytest.raises((Exception, asyncio.CancelledError)):
            await engine.negotiate(module.RemoteEngineEndpoint(1234, "fresh", 0, FakeOwner()), "session", "v=0",
                                   [{"urls": ["turn:relay.example:3478"], "username": "temporary", "credential": "temporary"}],
                                   asyncio.get_running_loop().time() + .5, on_attempt=attempts.append)
    assert live == set() and calls[-1].method == "DELETE"
    assert calls[-1].url.path.endswith(attempts[0])


class FakeOwner:
    def __init__(self):
        self.running = True

    def is_running(self):
        return self.running


@pytest.mark.asyncio
async def test_success_uses_unique_attempts_and_close_accepts_fresh_capability():
    from server.engine_remote import EngineRemoteClient, RemoteEngineEndpoint
    attempts = []
    calls = []
    async def handle(request):
        calls.append(request)
        assert request.url.host == "127.0.0.1"
        if request.method == "DELETE":
            assert request.headers["authorization"] == "Bearer renewed"
            return httpx.Response(204)
        body = json.loads(request.content)
        assert body["peer_id"] == attempts[-1]
        return httpx.Response(201, json={"peer_id": body["peer_id"], "generation": 0, "answer": "v=0"})
    owner = FakeOwner()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        engine = EngineRemoteClient(http)
        endpoint = RemoteEngineEndpoint(1234, "secret", 0, owner)
        for _ in range(2):
            answer = await engine.negotiate(endpoint, "session", "private-offer", [], asyncio.get_running_loop().time() + 1,
                                            on_attempt=attempts.append)
            assert answer.answer == "v=0"
            assert "v=0" not in repr(answer)
        assert len(set(attempts)) == 2
        await engine.close(RemoteEngineEndpoint(1234, "renewed", 1, owner), answer.peer_id)
        await engine.aclose()
        assert not http.is_closed
    assert calls[-1].url.path.endswith(answer.peer_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["redirect", "extra", "wrong_id", "bool_generation", "body_limit", "duplicate_fields"])
async def test_malformed_answer_is_rejected_without_following_redirect(failure):
    from server.engine_remote import EngineRemoteClient, RemoteEngineEndpoint
    calls = []
    async def handle(request):
        calls.append(request)
        assert request.url.host == "127.0.0.1"
        if request.method == "DELETE":
            return httpx.Response(204)
        body = json.loads(request.content)
        data = {"peer_id": body["peer_id"], "generation": 0, "answer": "v=0"}
        if failure == "redirect":
            return httpx.Response(307, headers={"Location": "http://evil.example/leak"})
        if failure == "extra": data["unexpected"] = "bad"
        if failure == "wrong_id": data["peer_id"] = "a" * 32
        if failure == "bool_generation": data["generation"] = False
        if failure == "body_limit": return httpx.Response(201, content=b" " * (1024 * 1024 + 1))
        if failure == "duplicate_fields": return httpx.Response(201, content=b'{"peer_id":"a","peer_id":"b","generation":0,"answer":"v=0"}')
        return httpx.Response(201, json=data)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True) as http:
        engine = EngineRemoteClient(http)
        with pytest.raises(Exception) as error:
            await engine.negotiate(RemoteEngineEndpoint(1234, "secret", 0, FakeOwner()), "session", "private-offer", [],
                                   asyncio.get_running_loop().time() + 1)
        assert "secret" not in str(error.value) and "private-offer" not in str(error.value)
    assert len(calls) == 2 and calls[-1].method == "DELETE"


@pytest.mark.asyncio
@pytest.mark.parametrize("offer,budget", [("x" * (128 * 1024 + 1), 1), ("", 1), ("v=0", -1), ("v=0", 31)], ids=["oversized", "empty", "expired", "over-budget"])
async def test_invalid_offer_or_budget_never_sends_capability(offer, budget):
    from server.engine_remote import EngineRemoteClient, RemoteEngineEndpoint
    calls = []
    def handle(request):
        calls.append(request)
        return httpx.Response(500)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        engine = EngineRemoteClient(http)
        with pytest.raises(Exception):
            await engine.negotiate(RemoteEngineEndpoint(1234, "secret", 0, FakeOwner()), "session", offer, [],
                                   asyncio.get_running_loop().time() + budget)
    assert calls == []


@pytest.mark.asyncio
async def test_failed_cleanup_is_retryable_by_published_attempt():
    from server.engine_remote import EngineRemoteClient, RemoteEngineEndpoint
    attempts = []
    live = set()
    deletes = 0
    async def handle(request):
        nonlocal deletes
        if request.method == "POST":
            body = json.loads(request.content)
            live.add(body["peer_id"])
            raise httpx.ReadError("offer and capability must not leak")
        deletes += 1
        if deletes == 1:
            raise httpx.ReadError("lost delete")
        live.discard(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(204)
    endpoint = RemoteEngineEndpoint(1234, "secret", 0, FakeOwner())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        engine = EngineRemoteClient(http)
        with pytest.raises(Exception):
            await engine.negotiate(endpoint, "session", "v=0", [], asyncio.get_running_loop().time() + 1,
                                   on_attempt=attempts.append)
        assert live == set(attempts)
        await engine.close(endpoint, attempts[0])
    assert live == set()


@pytest.mark.asyncio
async def test_caller_cancellation_supervises_cleanup_independently():
    from server.engine_remote import EngineRemoteClient, RemoteEngineEndpoint
    posted, deleting, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    live = set()
    async def handle(request):
        if request.method == "POST":
            live.add(json.loads(request.content)["peer_id"])
            posted.set()
            await asyncio.Event().wait()
        deleting.set()
        await release.wait()
        live.discard(request.url.path.rsplit("/", 1)[1])
        return httpx.Response(204)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        engine = EngineRemoteClient(http)
        work = asyncio.create_task(engine.negotiate(RemoteEngineEndpoint(1234, "secret", 0, FakeOwner()),
            "session", "v=0", [], asyncio.get_running_loop().time() + 1))
        await posted.wait()
        work.cancel()
        await deleting.wait()
        work.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError): await work
        await engine.aclose()
    assert live == set()


@pytest.mark.asyncio
async def test_port_reuse_does_not_receive_old_process_capability():
    from server.engine_remote import EngineRemoteClient, RemoteEngineEndpoint
    calls = []
    async def handle(request):
        calls.append(request)
        return httpx.Response(204)
    old, new = FakeOwner(), FakeOwner()
    old.running = False
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        engine = EngineRemoteClient(http)
        await engine.close(RemoteEngineEndpoint(1234, "new-secret", 0, new), "a" * 32)
        await engine.close(RemoteEngineEndpoint(1234, "old-secret", 0, old), "b" * 32)
    assert len(calls) == 1 and calls[0].headers["authorization"] == "Bearer new-secret"


def test_runtime_owner_changes_only_after_process_respawn():
    runtime, state = make_runtime(ready_ports=[(51000, 51001), (51000, 51001)])
    runtime.start()
    old = runtime.remote_endpoint()
    runtime.set_tier("1080")
    assert runtime.remote_endpoint().owner is old.owner
    state.engine_state.running = False
    assert runtime.remote_endpoint() is None
    assert runtime.check_once() == "respawned"
    new = runtime.remote_endpoint()
    assert new.owner is not old.owner
    assert not old.owner.is_running()
    runtime.stop()
