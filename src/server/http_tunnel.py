"""Tunnels the FastAPI app's HTTP + /input WebSocket traffic through a VPS
relay, for public-internet reachability without opening a home router port.

Two message families share one outbound WebSocket connection to the VPS
(the full protocol, including the WS-stream family, is built out across
this and the following task):
- http_request / http_response: the VPS wraps each proxied HTTP request as
  a single JSON envelope (small request/response bodies only — this app's
  surface is window-list/select/quality JSON plus small preview JPEGs, no
  streaming uploads or downloads), this module forwards it to the local
  FastAPI app via httpx and sends the response back the same way.
- ws_open / ws_open_ack / ws_message / ws_close: WebSocket stream forwarding
  for the /input endpoint (mouse/keyboard control); _run_ws_stream proxies
  inbound frames from the tunnel to a local WebSocket client and echoes
  responses back to the tunnel.
"""
import asyncio
import base64
import json
import logging

import httpx

log = logging.getLogger(__name__)

APP_BASE_URL = "http://127.0.0.1:8080"

_HOP_BY_HOP_HEADERS = {"host", "content-length", "connection", "transfer-encoding"}


def _filter_headers(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP_HEADERS}


async def _forward_http_request(client: httpx.AsyncClient, msg: dict) -> dict:
    body = base64.b64decode(msg["body"]) if msg.get("body") else b""
    resp = await client.request(
        msg["method"], APP_BASE_URL + msg["path"],
        headers=_filter_headers(msg.get("headers", {})), content=body,
    )
    return {
        "type": "http_response",
        "id": msg["id"],
        "status": resp.status_code,
        "headers": dict(resp.headers),
        "body": base64.b64encode(resp.content).decode(),
    }


async def _run_ws_stream(ws, open_frame: dict, inbound_queue: "asyncio.Queue",
                          local_ws_connect) -> None:
    """Proxy one /input WebSocket connection for the stream's lifetime.

    `inbound_queue` receives ws_message/ws_close frames destined for this
    stream_id (fed by the demultiplexing loop in run_tunnel_once, added in
    the next task) -- this function never reads `ws` directly, since it's
    shared across every concurrent stream.
    """
    stream_id = open_frame["id"]
    async with local_ws_connect(f"ws://127.0.0.1:8080{open_frame['path']}") as local_ws:
        await ws.send(json.dumps({"type": "ws_open_ack", "id": stream_id}))

        async def _pull_from_tunnel():
            while True:
                frame = await inbound_queue.get()
                if frame["type"] == "ws_close":
                    return
                await local_ws.send(frame["data"])

        async def _push_to_tunnel():
            async for data in local_ws:
                await ws.send(json.dumps(
                    {"type": "ws_message", "id": stream_id, "data": data}))

        pull_task = asyncio.ensure_future(_pull_from_tunnel())
        push_task = asyncio.ensure_future(_push_to_tunnel())
        _, pending = await asyncio.wait(
            {pull_task, push_task}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()

    await ws.send(json.dumps({"type": "ws_close", "id": stream_id}))
