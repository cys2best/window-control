import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import asyncio
import base64
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from server.http_tunnel import _filter_headers, _forward_http_request


def test_filter_headers_strips_hop_by_hop():
    headers = {
        "Host": "tunnel.example.com", "Content-Length": "123",
        "Connection": "keep-alive", "Cookie": "wc_session=abc",
        "Authorization": "Bearer native-token",
    }
    assert _filter_headers(headers) == {
        "Cookie": "wc_session=abc", "Authorization": "Bearer native-token",
    }


@pytest.mark.asyncio
async def test_forward_http_request_replays_auth_header_against_local_app():
    fake_response = MagicMock(status_code=200, headers={"content-type": "application/json"}, content=b'{"ok":true}')
    fake_client = AsyncMock()
    fake_client.request = AsyncMock(return_value=fake_response)
    msg = {
        "type": "http_request", "id": "stream-1", "method": "GET",
        "path": "/instances", "headers": {"Authorization": "Bearer native-token"},
        "body": "",
    }

    result = await _forward_http_request(fake_client, msg)

    fake_client.request.assert_awaited_once_with(
        "GET", "http://127.0.0.1:8080/instances",
        headers={"Authorization": "Bearer native-token"}, content=b"",
    )
    assert result == {
        "type": "http_response", "id": "stream-1", "status": 200,
        "headers": {"content-type": "application/json"},
        "body": base64.b64encode(b'{"ok":true}').decode(),
    }


@pytest.mark.asyncio
async def test_forward_http_request_decodes_base64_body():
    fake_response = MagicMock(status_code=200, headers={}, content=b"")
    fake_client = AsyncMock()
    fake_client.request = AsyncMock(return_value=fake_response)
    body = base64.b64encode(b'{"token":"s3cret"}').decode()

    await _forward_http_request(fake_client, {
        "type": "http_request", "id": "stream-2", "method": "POST",
        "path": "/login", "headers": {}, "body": body,
    })

    fake_client.request.assert_awaited_once_with(
        "POST", "http://127.0.0.1:8080/login", headers={},
        content=b'{"token":"s3cret"}',
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/stream", "/stream?1712345678"])
async def test_forward_http_request_refuses_streaming_path(path):
    fake_client = AsyncMock()
    fake_client.request = AsyncMock()

    result = await _forward_http_request(fake_client, {
        "type": "http_request", "id": "s9", "method": "GET",
        "path": path, "headers": {}, "body": "",
    })

    fake_client.request.assert_not_awaited()
    assert result["status"] == 501
    assert b"streaming" in base64.b64decode(result["body"])


def test_tunnel_http_client_has_generous_read_timeout():
    from server.http_tunnel import _new_http_client

    client = _new_http_client()
    assert client.timeout.read >= 30.0
    assert client.timeout.write >= 30.0
    assert client.timeout.pool >= 30.0
    assert client.timeout.connect <= 10.0


class _FakeDemuxWS:
    def __init__(self, incoming):
        self._incoming = list(incoming)
        self.sent = []

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

    fake_ws = _FakeDemuxWS([json.dumps({
        "type": "http_request", "id": "s1", "method": "GET",
        "path": "/instances", "headers": {}, "body": "",
    })])
    fake_response = MagicMock(status_code=200, headers={}, content=b"[]")
    fake_http = AsyncMock()
    fake_http.request = AsyncMock(return_value=fake_response)

    with pytest.raises(ConnectionError):
        await run_tunnel_once(
            "wss://tunnel.example.test/__tunnel/register", "tsecret",
            ws_connect=lambda url: fake_ws, http_client=fake_http,
        )

    sent = json.loads(fake_ws.sent[0])
    assert sent["type"] == "http_response"
    assert sent["id"] == "s1"
    assert sent["status"] == 200


@pytest.mark.asyncio
async def test_ws_open_is_ignored_without_creating_a_background_task(monkeypatch):
    from server import http_tunnel

    fake_ws = _FakeDemuxWS([json.dumps({
        "type": "ws_open", "id": "s1", "path": "/input", "headers": {},
    })])
    task_calls = []
    original_ensure_future = http_tunnel.asyncio.ensure_future

    def record_task(coro):
        task_calls.append(coro)
        return original_ensure_future(coro)

    monkeypatch.setattr(http_tunnel.asyncio, "ensure_future", record_task)
    with pytest.raises(ConnectionError):
        await http_tunnel.run_tunnel_once(
            "wss://tunnel.example.test/__tunnel/register", "tsecret",
            ws_connect=lambda url: fake_ws, http_client=AsyncMock(),
        )

    assert task_calls == []
    assert fake_ws.sent == []


@pytest.mark.asyncio
async def test_respond_http_sends_502_envelope_when_forward_fails(caplog):
    from server.http_tunnel import run_tunnel_once

    fake_ws = _FakeDemuxWS([json.dumps({
        "type": "http_request", "id": "s1", "method": "GET",
        "path": "/instances", "headers": {}, "body": "",
    })])
    fake_http = AsyncMock()
    fake_http.request = AsyncMock(side_effect=httpx.ReadTimeout("local app hung"))

    with caplog.at_level(logging.ERROR, logger="server.http_tunnel"):
        with pytest.raises(ConnectionError):
            await run_tunnel_once(
                "wss://tunnel.example.test/__tunnel/register", "tsecret",
                ws_connect=lambda url: fake_ws, http_client=fake_http,
            )

    sent = json.loads(fake_ws.sent[0])
    assert sent["type"] == "http_response"
    assert sent["status"] == 502
    assert b"failed" in base64.b64decode(sent["body"])
    assert any("failed" in record.getMessage() for record in caplog.records)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_frame", [
    "{not json at all", '"a bare json string"', '{"id": "s1"}',
    '{"type": "http_request"}',
])
async def test_run_tunnel_once_survives_malformed_frame(bad_frame, caplog):
    from server.http_tunnel import run_tunnel_once

    good_frame = json.dumps({
        "type": "http_request", "id": "s2", "method": "GET",
        "path": "/instances", "headers": {}, "body": "",
    })
    fake_ws = _FakeDemuxWS([bad_frame, good_frame])
    fake_response = MagicMock(status_code=200, headers={}, content=b"[]")
    fake_http = AsyncMock()
    fake_http.request = AsyncMock(return_value=fake_response)

    with caplog.at_level(logging.WARNING, logger="server.http_tunnel"):
        with pytest.raises(ConnectionError):
            await run_tunnel_once(
                "wss://tunnel.example.test/__tunnel/register", "tsecret",
                ws_connect=lambda url: fake_ws, http_client=fake_http,
            )

    assert any("malformed frame" in record.getMessage() for record in caplog.records)
    assert json.loads(fake_ws.sent[0])["id"] == "s2"


@pytest.mark.asyncio
async def test_run_tunnel_with_reconnect_retries_after_unexpected_exception(caplog, monkeypatch):
    from server import http_tunnel

    calls = 0

    async def fake_run_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise ValueError("unexpected")

    async def no_wait(_seconds):
        if calls >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(http_tunnel, "run_tunnel_once", fake_run_once)
    with caplog.at_level(logging.ERROR, logger="server.http_tunnel"):
        with pytest.raises(asyncio.CancelledError):
            await http_tunnel.run_tunnel_with_reconnect(
                "wss://tunnel.example.test/__tunnel/register", "tsecret",
                sleep=no_wait,
            )

    assert calls == 3
    assert any("unexpected failure" in record.getMessage() for record in caplog.records)
