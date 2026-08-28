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
import asyncio
import logging

import httpx
import websockets

log = logging.getLogger(__name__)

# httpx's default timeout is 5s on everything, which is too short for a WHEP
# POST: aiortc's setLocalDescription() awaits full ICE gathering before
# returning the answer, including a TURN allocation round-trip to the VPS --
# on a slow link (or one hitting coturn's default deny-list on a candidate,
# see whep_app.py's _lan_ice_servers) that alone can approach or exceed 5s.
# A tripped ReadTimeout here doesn't just log a warning: relay_one_instance()
# has already sent the offer and is waiting on the one response for it, so
# this exception ends that WS session and forces a full reconnect, discarding
# a negotiation that may well have still been about to succeed. Matches
# http_tunnel.py's TUNNEL_HTTP_TIMEOUT budget for the same kind of local
# slow-negotiation call.
_WHEP_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=5.0)


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
    point an exception propagates to the caller (typically
    `websockets.exceptions.ConnectionClosedError` from `ws.recv()` on a
    normal disconnect, or `ConnectionRefusedError` if the VPS is
    unreachable) — reconnect/backoff is the caller's responsibility, not
    this function's.
    """
    client = http_client or httpx.AsyncClient(timeout=_WHEP_HTTP_TIMEOUT)
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


async def run_bridge_with_reconnect(
    instance_name: str,
    signaling_url: str,
    whep_port: int,
    backoff_seconds: float = 2.0,
    ws_connect=websockets.connect,
    http_client: httpx.AsyncClient | None = None,
    sleep=asyncio.sleep,
) -> None:
    """Run relay_one_instance() forever, reconnecting after each disconnect.

    relay_one_instance() raises when its WS connection ends (by design --
    reconnect is documented as the caller's responsibility, not that
    function's). Against the real `websockets` library that's typically
    `websockets.exceptions.WebSocketException` (e.g. `ConnectionClosedError`
    from `recv()`), not a bare `ConnectionError`; `ConnectionError`/`OSError`
    are still caught for TCP-level failures (e.g. `ConnectionRefusedError`
    if the VPS is unreachable), and `httpx.HTTPError` for the local WHEP
    POST failing. This wrapper is that caller: catch those, log, wait
    backoff_seconds, try again. An asyncio.CancelledError (the task being
    cancelled by whoever started it, e.g. on instance switch) propagates
    normally and ends the loop -- that is the intended way to stop this
    function, not a return value.

    When the caller doesn't inject an `http_client`, one `httpx.AsyncClient`
    is created and reused for the whole retry loop (not a fresh one per
    iteration, which would leak a connection pool on every reconnect).
    """
    async def _loop(client: httpx.AsyncClient | None) -> None:
        while True:
            try:
                log.info("bridge %s: connecting", instance_name)
                await relay_one_instance(
                    instance_name, signaling_url, whep_port,
                    ws_connect=ws_connect, http_client=client,
                )
            except (ConnectionError, OSError, websockets.exceptions.WebSocketException,
                    httpx.HTTPError) as exc:
                log.warning("bridge %s: relay ended (%s), retrying in %ss",
                            instance_name, exc.__class__.__name__, backoff_seconds)
            await sleep(backoff_seconds)
            await asyncio.sleep(0)  # Ensure cancellation can be delivered

    if http_client is not None:
        await _loop(http_client)
    else:
        async with httpx.AsyncClient(timeout=_WHEP_HTTP_TIMEOUT) as client:
            await _loop(client)
