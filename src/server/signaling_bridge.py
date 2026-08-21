"""Relays SDP offer/answer text between the VPS signaling relay and a local
mediamtx WHEP endpoint.

This is deliberately thin: mediamtx already does all real WebRTC/RTP work
(negotiation, packetization, codec handling) via its local WHEP endpoint,
proven working against Tailscale clients today. The only thing missing for
public-internet clients is signaling reachability, since mediamtx's WHEP
endpoint is local-network-only. This module bridges that gap without adding
any new WebRTC logic: it connects outbound to the VPS signaling server as
`role=engine`, waits for an SDP offer, POSTs it to mediamtx's local WHEP
endpoint, and relays mediamtx's SDP answer back over the same WebSocket.

Only SDP offer/answer text passes through the relay — no JSON envelope, no
ICE candidate handling here. Media flows browser<->mediamtx directly once
negotiated; this bridge is out of the media path entirely after the answer
is sent.
"""
import httpx
import websockets


async def relay_one_instance(
    instance_name: str,
    signaling_url: str,
    whep_port: int,
    ws_connect=websockets.connect,
    http_client: httpx.AsyncClient | None = None,
) -> None:
    """Relay one signaling session for a single mediamtx instance.

    Connects to `{signaling_url}/?session={instance_name}&role=engine`,
    waits for an SDP offer (a raw SDP text message, not JSON), POSTs it to
    `http://127.0.0.1:{whep_port}/{instance_name}/whep`, and sends mediamtx's
    SDP answer back over the same WebSocket connection. Loops for
    subsequent offers on the same connection until it closes, at which
    point the underlying `ConnectionError` propagates to the caller —
    reconnect/backoff is the caller's responsibility, not this function's.
    """
    client = http_client or httpx.AsyncClient()
    url = f"{signaling_url}/?session={instance_name}&role=engine"
    whep_url = f"http://127.0.0.1:{whep_port}/{instance_name}/whep"

    async with ws_connect(url) as ws:
        while True:
            offer_sdp = await ws.recv()
            response = await client.post(
                whep_url,
                content=offer_sdp,
                headers={"Content-Type": "application/sdp"},
            )
            response.raise_for_status()
            await ws.send(response.text)
