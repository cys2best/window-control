"""Tunnel bounded HTTP request/response envelopes through a VPS relay."""
import asyncio
import base64
import json
import logging

import httpx
import websockets

log = logging.getLogger(__name__)

APP_BASE_URL = "http://127.0.0.1:8080"

_HOP_BY_HOP_HEADERS = {"host", "content-length", "connection", "transfer-encoding"}

# httpx's default timeout is 5s on every operation, which is far too short for
# the routes this tunnel actually carries: POST /instances/{id}/select may
# cold-start an engine runtime and take tens of seconds; even the cheaper
# quality-tier switch is ~1.8s of blocking restart.
# The read/write/pool budget is therefore generous, while `connect` stays short
# so a local app that isn't listening at all fails fast instead of making the
# public browser wait a minute for an error.
TUNNEL_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=5.0)

def _new_http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=TUNNEL_HTTP_TIMEOUT)


def _filter_headers(headers: dict) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in _HOP_BY_HOP_HEADERS}


def _error_response(stream_id, status: int, body: bytes) -> dict:
    return {
        "type": "http_response",
        "id": stream_id,
        "status": status,
        "headers": {"content-type": "text/plain"},
        "body": base64.b64encode(body).decode(),
    }


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


async def run_tunnel_once(
    tunnel_url: str, tunnel_secret: str,
    ws_connect=websockets.connect,
    http_client: httpx.AsyncClient | None = None,
) -> None:
    """Hold one tunnel connection and dispatch only HTTP envelopes."""
    client = http_client or _new_http_client()
    url = f"{tunnel_url}?token={tunnel_secret}"

    # HTTP responses are dispatched fire-and-forget so one slow request never
    # blocks the demux loop. A task whose outcome is
    # never retrieved either leaks silently on success or logs nothing but
    # an unhandled "Task exception was never retrieved" warning on failure.
    # Track every dispatched task here so we can wait for genuinely
    # in-flight ones once the connection ends (see the `finally` block
    # below); the done-callback itself is what actually surfaces a
    # failure -- it fires the instant a task completes, whether that's
    # mid-connection or at teardown, so a request/stream failure is never
    # silently handed to Python's default "Task exception was never
    # retrieved" path.
    background_tasks: set[asyncio.Task] = set()

    def _dispatch(coro) -> None:
        task = asyncio.ensure_future(coro)
        background_tasks.add(task)

        def _on_task_done(t: asyncio.Task) -> None:
            background_tasks.discard(t)
            if t.cancelled():
                return
            exc = t.exception()
            if exc is not None:
                log.error("tunnel: background task failed", exc_info=exc)

        task.add_done_callback(_on_task_done)

    try:
        async with ws_connect(url) as ws:
            async for raw in ws:
                # A malformed frame must never kill the tunnel: json.loads
                # raising JSONDecodeError (or a well-formed non-dict / a dict
                # missing `type`/`id` raising TypeError/KeyError) would escape
                # run_tunnel_with_reconnect's caught exceptions and permanently
                # kill the tunnel task. Mirrors the JSON.parse try/catch on the
                # VPS side of this same protocol boundary (server.js).
                try:
                    frame = json.loads(raw)
                    ftype = frame["type"]
                    stream_id = frame.get("id")
                    if ftype == "http_request" and stream_id is None:
                        raise KeyError("id")
                except (ValueError, TypeError, KeyError) as exc:
                    log.warning("tunnel: ignoring malformed frame (%s): %.200r",
                                exc.__class__.__name__, raw)
                    continue

                if ftype == "http_request":
                    _dispatch(_respond_http(ws, client, frame))
                else:
                    log.warning("tunnel: ignoring unsupported frame type %r", ftype)
    finally:
        if background_tasks:
            # Anything still in `background_tasks` here was genuinely
            # in-flight when the connection loop exited (already-completed
            # tasks were removed by `_on_task_done` as soon as they
            # finished, and had their outcome logged there already). This
            # is just "don't return with dangling work" -- not a second
            # place exceptions get surfaced, so don't re-log them here.
            await asyncio.gather(*background_tasks, return_exceptions=True)


async def _respond_http(ws, client: httpx.AsyncClient, frame: dict) -> None:
    try:
        response = await _forward_http_request(client, frame)
    except Exception as exc:
        # Without this, a failed forward (local app down, read timeout,
        # malformed base64 body, ...) sends *nothing* back: the VPS's
        # pendingHttp entry and the browser's request both hang. Log it (the
        # background-task logging in run_tunnel_once stays useful for other
        # failures) *and* give the far side a real answer.
        log.error("tunnel: forwarding %s %s failed", frame.get("method"),
                  frame.get("path"), exc_info=exc)
        response = _error_response(
            frame.get("id"), 502, b"Tunnel: local app request failed")
    await ws.send(json.dumps(response))


async def run_tunnel_with_reconnect(
    tunnel_url: str, tunnel_secret: str,
    backoff_seconds: float = 2.0,
    ws_connect=websockets.connect,
    http_client: httpx.AsyncClient | None = None,
    sleep=asyncio.sleep,
) -> None:
    """Run run_tunnel_once() forever, reconnecting after each disconnect.

    Mirrors signaling_bridge.run_bridge_with_reconnect: catches
    ConnectionError/OSError/websockets.exceptions.WebSocketException,
    logs, waits backoff_seconds, retries. asyncio.CancelledError propagates
    normally (app shutdown / config change stops this the same way).
    """
    async def _loop(client: httpx.AsyncClient | None) -> None:
        while True:
            try:
                log.info("tunnel: connecting to %s", tunnel_url)
                await run_tunnel_once(
                    tunnel_url, tunnel_secret,
                    ws_connect=ws_connect, http_client=client,
                )
            except (ConnectionError, OSError, websockets.exceptions.WebSocketException) as exc:
                log.warning("tunnel: connection ended (%s), retrying in %ss",
                            exc.__class__.__name__, backoff_seconds)
            except Exception as exc:
                # The point of the reconnect wrapper is that nothing short of
                # app shutdown leaves the tunnel permanently dead. Anything
                # unexpected out of run_tunnel_once gets logged loudly and
                # retried instead of silently killing the task forever.
                # (asyncio.CancelledError is a BaseException, so shutdown still
                # propagates through this clause untouched.)
                log.error("tunnel: unexpected failure, retrying in %ss",
                          backoff_seconds, exc_info=exc)
            await sleep(backoff_seconds)
            await asyncio.sleep(0)  # ensure cancellation can be delivered

    if http_client is not None:
        await _loop(http_client)
    else:
        async with _new_http_client() as client:
            await _loop(client)
