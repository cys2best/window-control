import asyncio
import json

import httpx
import pytest

from broker.cloudflare_turn import CloudflareTurnError, fetch_turn_server
from remote_protocol import SessionIceBundle


CLOUDFLARE_RESPONSE = {
    "iceServers": [
        {"urls": ["stun:stun.cloudflare.com:3478", "stun:stun.cloudflare.com:53"]},
        {
            "urls": [
                "turn:turn.cloudflare.com:3478?transport=udp",
                "turn:turn.cloudflare.com:53?transport=udp",
                "turn:turn.cloudflare.com:3478?transport=tcp",
                "turn:turn.cloudflare.com:80?transport=tcp",
                "turns:turn.cloudflare.com:5349?transport=tcp",
                "turns:turn.cloudflare.com:443?transport=tcp",
            ],
            "username": "cf-user",
            "credential": "cf-credential",
            "extra": "ignored",
        },
    ]
}


def fetch(handler, key_id="abc123", token="secret-token"):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_turn_server(key_id, token, client=client)
    return asyncio.run(run())


def test_requests_credentials_with_bearer_token_and_ttl():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(201, json=CLOUDFLARE_RESPONSE)

    fetch(handler)
    request, = seen
    assert request.method == "POST"
    assert str(request.url) == (
        "https://rtc.live.cloudflare.com/v1/turn/keys/abc123/credentials/generate-ice-servers")
    assert request.headers["Authorization"] == "Bearer secret-token"
    assert json.loads(request.content) == {"ttl": 3600}


def test_response_is_sanitized_to_valid_turn_server():
    result = fetch(lambda request: httpx.Response(201, json=CLOUDFLARE_RESPONSE))
    assert set(result) == {"urls", "username", "credential"}
    assert result["username"] == "cf-user"
    assert result["credential"] == "cf-credential"
    assert result["urls"] == [
        "turn:turn.cloudflare.com:3478?transport=udp",
        "turn:turn.cloudflare.com:3478?transport=tcp",
        "turn:turn.cloudflare.com:80?transport=tcp",
        "turns:turn.cloudflare.com:5349?transport=tcp",
        "turns:turn.cloudflare.com:443?transport=tcp",
    ]
    SessionIceBundle(ice_servers=[result], expires_at=1, renew_after=3300, relay_available=True)


@pytest.mark.parametrize("response", [
    httpx.Response(401, json={"error": "nope"}),
    httpx.Response(500, text="boom"),
    httpx.Response(201, text="not json"),
    httpx.Response(201, json={"iceServers": []}),
])
def test_http_errors_and_bad_bodies_raise_without_leaking_token(response):
    with pytest.raises(CloudflareTurnError) as caught:
        fetch(lambda request: response)
    assert "secret-token" not in str(caught.value)


def test_invalid_key_id_is_rejected_before_request():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(201, json=CLOUDFLARE_RESPONSE)

    with pytest.raises(CloudflareTurnError):
        fetch(handler, key_id="../x")
    assert calls == []
