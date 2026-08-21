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
"""
import base64
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
