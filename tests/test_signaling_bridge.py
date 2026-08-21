import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from server.signaling_bridge import relay_one_instance


class _FakeWS:
    """Minimal stand-in for a websockets connection: yields queued incoming
    messages, records outgoing ones, and lets the test end the loop by
    raising on the Nth receive."""
    def __init__(self, incoming: list[str]):
        self._incoming = list(incoming)
        self.sent: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def recv(self):
        if not self._incoming:
            raise ConnectionError("no more fake messages")
        return self._incoming.pop(0)

    async def send(self, msg):
        self.sent.append(msg)


@pytest.mark.asyncio
async def test_relay_forwards_offer_to_whep_and_answer_back():
    fake_ws = _FakeWS(["v=0 FAKE OFFER SDP"])

    def fake_connect(url):
        assert "session=instance0" in url
        assert "role=engine" in url
        return fake_ws

    fake_response = MagicMock()
    fake_response.text = "v=0 FAKE ANSWER SDP"
    fake_response.raise_for_status = MagicMock()
    fake_http = AsyncMock()
    fake_http.post = AsyncMock(return_value=fake_response)

    with pytest.raises(ConnectionError):
        await relay_one_instance(
            "instance0", "ws://vps.example.test:8443", 8889,
            ws_connect=fake_connect, http_client=fake_http,
        )

    fake_http.post.assert_awaited_once_with(
        "http://127.0.0.1:8889/instance0/whep",
        content="v=0 FAKE OFFER SDP",
        headers={"Content-Type": "application/sdp"},
    )
    assert fake_ws.sent == ["v=0 FAKE ANSWER SDP"]
