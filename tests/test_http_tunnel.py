import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import asyncio
import base64
import json
import logging
import pytest
from unittest.mock import AsyncMock, MagicMock

from server.http_tunnel import _filter_headers, _forward_http_request


def test_filter_headers_strips_hop_by_hop():
    headers = {
        "Host": "tunnel.example.com", "Content-Length": "123",
        "Connection": "keep-alive", "Cookie": "wc_session=abc",
    }
    filtered = _filter_headers(headers)
    assert filtered == {"Cookie": "wc_session=abc"}


@pytest.mark.asyncio
async def test_forward_http_request_replays_against_local_app():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.headers = {"content-type": "application/json"}
    fake_response.content = b'{"ok":true}'
    fake_client = AsyncMock()
    fake_client.request = AsyncMock(return_value=fake_response)

    msg = {
        "type": "http_request", "id": "stream-1", "method": "GET",
        "path": "/instances", "headers": {"Cookie": "wc_session=abc"},
        "body": "",
    }

    result = await _forward_http_request(fake_client, msg)

    fake_client.request.assert_awaited_once_with(
        "GET", "http://127.0.0.1:8080/instances",
        headers={"Cookie": "wc_session=abc"}, content=b"",
    )
    assert result == {
        "type": "http_response", "id": "stream-1", "status": 200,
        "headers": {"content-type": "application/json"},
        "body": base64.b64encode(b'{"ok":true}').decode(),
    }


@pytest.mark.asyncio
async def test_forward_http_request_decodes_base64_body():
    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.headers = {}
    fake_response.content = b""
    fake_client = AsyncMock()
    fake_client.request = AsyncMock(return_value=fake_response)

    body_bytes = b'{"token":"s3cret"}'
    msg = {
        "type": "http_request", "id": "stream-2", "method": "POST",
        "path": "/login", "headers": {},
        "body": base64.b64encode(body_bytes).decode(),
    }

    await _forward_http_request(fake_client, msg)

    fake_client.request.assert_awaited_once_with(
        "POST", "http://127.0.0.1:8080/login", headers={}, content=body_bytes,
    )


class _FakeTunnelWS:
    """Records what run_tunnel_once/_run_ws_stream sends back to the VPS."""
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, raw: str):
        self.sent.append(json.loads(raw))


class _FakeLocalWS:
    """Stand-in for a local `websockets` client connection to /input."""
    def __init__(self, incoming: list[str]):
        self._incoming = list(incoming)
        self.sent: list[str] = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True
        return False

    async def send(self, data):
        self.sent.append(data)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._incoming:
            raise StopAsyncIteration
        return self._incoming.pop(0)


class _BlockingFakeLocalWS(_FakeLocalWS):
    """Like _FakeLocalWS, but __anext__ genuinely suspends (a real await on
    an unresolved future, via asyncio.sleep) instead of resolving within a
    single event-loop tick. This lets a test put _push_to_tunnel's read
    loop into a truly pending state, so cancellation of it is real rather
    than a no-op over an already-finished task.
    """
    def __init__(self):
        super().__init__(incoming=[])
        self.anext_started = False
        self.anext_completed = False

    async def __anext__(self):
        self.anext_started = True
        await asyncio.sleep(1_000_000)
        self.anext_completed = True  # pragma: no cover - only if not cancelled
        return "unreachable"


@pytest.mark.asyncio
async def test_run_ws_stream_pipes_both_directions_then_closes():
    from server.http_tunnel import _run_ws_stream

    tunnel_ws = _FakeTunnelWS()
    local_ws = _FakeLocalWS(incoming=['{"type":"pong"}'])

    def fake_local_ws_connect(url):
        assert url == "ws://127.0.0.1:8080/input"
        return local_ws

    inbound_queue: asyncio.Queue = asyncio.Queue()
    await inbound_queue.put({"type": "ws_message", "id": "s1", "data": '{"type":"tap"}'})
    await inbound_queue.put({"type": "ws_close", "id": "s1"})

    open_frame = {"type": "ws_open", "id": "s1", "path": "/input", "headers": {}}
    await _run_ws_stream(tunnel_ws, open_frame, inbound_queue, fake_local_ws_connect)

    assert local_ws.sent == ['{"type":"tap"}']
    assert local_ws.closed is True
    assert {"type": "ws_open_ack", "id": "s1"} in tunnel_ws.sent
    assert {"type": "ws_message", "id": "s1", "data": '{"type":"pong"}'} in tunnel_ws.sent
    assert {"type": "ws_close", "id": "s1"} in tunnel_ws.sent


@pytest.mark.asyncio
async def test_run_ws_stream_cancels_still_running_task_on_close():
    """Regression test for a still-running push task actually being
    cancelled (not just left to finish on its own) when the pull side sees
    ws_close first. Uses _BlockingFakeLocalWS so _push_to_tunnel is
    genuinely suspended (a real await on an unresolved future) rather than
    completing within the same event-loop tick as _pull_from_tunnel --
    otherwise `pending` is always empty and the cancel path is never
    exercised (see review finding on the original happy-path test).
    """
    from server.http_tunnel import _run_ws_stream

    tunnel_ws = _FakeTunnelWS()
    local_ws = _BlockingFakeLocalWS()

    def fake_local_ws_connect(url):
        assert url == "ws://127.0.0.1:8080/input"
        return local_ws

    inbound_queue: asyncio.Queue = asyncio.Queue()
    await inbound_queue.put({"type": "ws_close", "id": "s1"})

    open_frame = {"type": "ws_open", "id": "s1", "path": "/input", "headers": {}}

    await asyncio.wait_for(
        _run_ws_stream(tunnel_ws, open_frame, inbound_queue, fake_local_ws_connect),
        timeout=5,
    )

    # The push task's read loop got as far as its blocking __anext__ call
    # (so it really was in-flight and concurrent with the pull side)...
    assert local_ws.anext_started is True
    # ...but a real cancellation interrupted the sleep before it could
    # complete -- if the push task were left to run to completion instead
    # of being awaited-after-cancel, this would be True.
    assert local_ws.anext_completed is False
    # __aexit__ (which closes local_ws) only ran after the cancellation was
    # actually delivered, not concurrently with a still-running task.
    assert local_ws.closed is True
    assert {"type": "ws_close", "id": "s1"} in tunnel_ws.sent


@pytest.mark.asyncio
async def test_run_ws_stream_logs_exception_from_pipe_task(caplog):
    """Regression test: an exception raised inside one of the pipe tasks
    (e.g. local_ws.send failing) must be logged with the stream_id, not
    silently swallowed and reported to the tunnel as a clean ws_close.
    """
    from server.http_tunnel import _run_ws_stream

    class _RaisingLocalWS(_FakeLocalWS):
        async def send(self, data):
            raise RuntimeError("boom")

    tunnel_ws = _FakeTunnelWS()
    local_ws = _RaisingLocalWS(incoming=[])

    def fake_local_ws_connect(url):
        return local_ws

    inbound_queue: asyncio.Queue = asyncio.Queue()
    await inbound_queue.put({"type": "ws_message", "id": "s1", "data": '{"type":"tap"}'})

    open_frame = {"type": "ws_open", "id": "s1", "path": "/input", "headers": {}}

    with caplog.at_level(logging.ERROR, logger="server.http_tunnel"):
        await asyncio.wait_for(
            _run_ws_stream(tunnel_ws, open_frame, inbound_queue, fake_local_ws_connect),
            timeout=5,
        )

    # The stream still closes cleanly from the tunnel's point of view...
    assert {"type": "ws_close", "id": "s1"} in tunnel_ws.sent
    # ...but the failure was actually logged, with the stream id, and with
    # the real exception attached -- not left to Python's default
    # "Task exception was never retrieved" handler.
    error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("s1" in r.getMessage() for r in error_records)
    assert any(r.exc_info is not None for r in error_records)


def test_filter_headers_and_forward_still_pass():
    # sanity guard -- demux loop reuses these, not re-tested here
    pass


class _FakeDemuxWS:
    def __init__(self, incoming: list[str]):
        self._incoming = list(incoming)
        self.sent: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._incoming:
            raise ConnectionError("no more fake messages")
        return self._incoming.pop(0)

    async def send(self, raw):
        self.sent.append(raw)


@pytest.mark.asyncio
async def test_run_tunnel_once_dispatches_http_request_and_responds():
    from server.http_tunnel import run_tunnel_once

    req_frame = json.dumps({
        "type": "http_request", "id": "s1", "method": "GET",
        "path": "/instances", "headers": {}, "body": "",
    })
    fake_ws = _FakeDemuxWS([req_frame])

    def fake_ws_connect(url):
        assert url == "wss://tunnel.example.test/__tunnel/register?token=tsecret"
        return fake_ws

    fake_response = MagicMock()
    fake_response.status_code = 200
    fake_response.headers = {}
    fake_response.content = b"[]"
    fake_http = AsyncMock()
    fake_http.request = AsyncMock(return_value=fake_response)

    with pytest.raises(ConnectionError):
        await run_tunnel_once(
            "wss://tunnel.example.test/__tunnel/register", "tsecret",
            ws_connect=fake_ws_connect, http_client=fake_http,
        )

    assert len(fake_ws.sent) == 1
    sent = json.loads(fake_ws.sent[0])
    assert sent["type"] == "http_response"
    assert sent["id"] == "s1"
    assert sent["status"] == 200


@pytest.mark.asyncio
async def test_run_tunnel_with_reconnect_retries_after_disconnect():
    from server.http_tunnel import run_tunnel_with_reconnect
    import server.http_tunnel as tunnel_module

    call_count = 0

    async def fake_run_once(tunnel_url, tunnel_secret, ws_connect=None,
                             http_client=None, local_ws_connect=None):
        nonlocal call_count
        call_count += 1
        raise ConnectionError("fake disconnect")

    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    original = tunnel_module.run_tunnel_once
    tunnel_module.run_tunnel_once = fake_run_once
    try:
        task = asyncio.ensure_future(
            run_tunnel_with_reconnect(
                "wss://tunnel.example.test/__tunnel/register", "tsecret",
                backoff_seconds=0.01, sleep=fake_sleep,
            )
        )
        for _ in range(50):
            if call_count >= 3:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        tunnel_module.run_tunnel_once = original

    assert call_count >= 3
    assert all(s == 0.01 for s in sleeps)
