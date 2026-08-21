import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import base64
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
