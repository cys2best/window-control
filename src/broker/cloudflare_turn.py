"""Cloudflare Realtime TURN credentials. The API token never leaves the broker."""

import re

import httpx

from remote_protocol import validate_ice_url

CLOUDFLARE_TURN_URL = "https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}/credentials/generate-ice-servers"


class CloudflareTurnError(RuntimeError):
    pass


async def fetch_turn_server(key_id: str, api_token: str, *, ttl: int = 3600, client: httpx.AsyncClient) -> dict:
    """Returns one TURN server dict that SessionIceBundle accepts, or raises CloudflareTurnError."""
    if not isinstance(key_id, str) or not re.fullmatch(r"[A-Za-z0-9]{1,64}", key_id):
        raise CloudflareTurnError("invalid Cloudflare TURN key id")
    try:
        response = await client.post(
            CLOUDFLARE_TURN_URL.format(key_id=key_id),
            headers={"Authorization": f"Bearer {api_token}"},
            json={"ttl": ttl},
        )
    except httpx.HTTPError as exc:
        raise CloudflareTurnError(f"Cloudflare TURN request failed: {type(exc).__name__}") from None
    if response.status_code not in (200, 201):
        raise CloudflareTurnError(f"Cloudflare TURN returned HTTP {response.status_code}")
    try:
        servers = response.json().get("iceServers")
    except (ValueError, AttributeError):
        raise CloudflareTurnError("Cloudflare TURN returned an invalid body") from None
    if isinstance(servers, dict):
        servers = [servers]
    if not isinstance(servers, list):
        raise CloudflareTurnError("Cloudflare TURN returned an invalid body")
    for server in servers:
        if not isinstance(server, dict):
            continue
        urls = server.get("urls")
        urls = [urls] if isinstance(urls, str) else urls
        username, credential = server.get("username"), server.get("credential")
        if (not isinstance(urls, list) or not isinstance(username, str) or not isinstance(credential, str)
                or not 0 < len(username) <= 4096 or not 0 < len(credential) <= 4096):
            continue
        kept = [url for url in urls if _usable_turn_url(url)][:16]
        if kept:
            return {"urls": kept, "username": username, "credential": credential}
    raise CloudflareTurnError("Cloudflare TURN returned no usable relay server")


def _usable_turn_url(url) -> bool:
    try:
        validate_ice_url(url)
    except ValueError:
        return False
    return url.startswith(("turn:", "turns:")) and not re.search(r":53(?:\?|$)", url)
