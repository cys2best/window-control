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
import websockets

log = logging.getLogger(__name__)

APP_BASE_URL = "http://127.0.0.1:8080"

_HOP_BY_HOP_HEADERS = {"host", "content-length", "connection", "transfer-encoding"}

# httpx's default timeout is 5s on every operation, which is far too short for
# the routes this tunnel actually carries: POST /instances/{id}/select blocks
# on a scrcpy/adb session start (cold-starting a dead session can take tens of
# seconds; even the cheaper quality-tier switch is ~1.8s of blocking restart).
# The read/write/pool budget is therefore generous, while `connect` stays short
# so a local app that isn't listening at all fails fast instead of making the
# public browser wait a minute for an error.
TUNNEL_HTTP_TIMEOUT = httpx.Timeout(60.0, connect=5.0)

# Responses this module refuses to forward. _forward_http_request buffers the
# whole response body (`resp.content`) before wrapping it in one JSON envelope,
# which is fine for this app's JSON + small preview JPEGs but catastrophic for
# GET /stream: an endless multipart/x-mixed-replace MJPEG stream would
# accumulate in the PC's RAM forever, never completing, never responding. The
# tunnel is small-bodies-only by design (see module docstring); say so
# explicitly with a 501 rather than OOMing the PC.
_UNSUPPORTED_STREAMING_PATHS = {"/stream"}

# /internal/* is meant for mediamtx's local publish_hook.py only (see app.py's
# localhost-only guard on these routes). This module forwards every request to
# the local app over 127.0.0.1 itself, so a request relayed from the public
# internet would look identical to publish_hook.py's own call by source IP
# alone -- the tunnel must refuse to forward these paths at all, mirroring the
# _UNSUPPORTED_STREAMING_PATHS refusal above. A prefix check (not membership in
# a set) since /internal/ has many sub-paths.
_INTERNAL_PATH_PREFIX = "/internal/"

_STREAM_UNSUPPORTED_BODY = (
    b"The public tunnel does not support streaming responses (MJPEG). "
    b"Use the WebRTC stream path instead."
)


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


def _cookie_headers(headers: dict) -> dict:
    """Extract just the Cookie header (case-insensitively) from a proxied
    header dict.

    Only the cookie is forwarded onto the local WebSocket handshake: the rest
    of the browser's headers that survive _filter_headers (`upgrade`,
    `sec-websocket-key`, `sec-websocket-version`, ...) belong to the *browser's*
    handshake with the VPS and would corrupt the new handshake this process
    performs against the local app.
    """
    for key, value in headers.items():
        if key.lower() == "cookie":
            return {"Cookie": value}
    return {}


async def _forward_http_request(client: httpx.AsyncClient, msg: dict) -> dict:
    path_only = msg["path"].split("?", 1)[0]
    if path_only in _UNSUPPORTED_STREAMING_PATHS:
        log.warning("tunnel: refusing to forward streaming path %s", msg["path"])
        return _error_response(msg["id"], 501, _STREAM_UNSUPPORTED_BODY)
    if path_only.startswith(_INTERNAL_PATH_PREFIX):
        log.warning("tunnel: refusing to forward internal path %s", msg["path"])
        return _error_response(msg["id"], 501, _STREAM_UNSUPPORTED_BODY)

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
    # The browser's real headers ride along on the ws_open frame. The Cookie
    # header MUST be replayed onto the local handshake: with AUTH_TOKEN set
    # (mandatory whenever the tunnel is configured, see create_app's guard),
    # app.py's auth gate closes a cookie-less /input handshake with 1008 and
    # the whole control channel is dead. `additional_headers` is the
    # websockets>=14 spelling of the old `extra_headers` kwarg (see the
    # websockets floor pinned in requirements.txt / pyproject.toml).
    auth_headers = _cookie_headers(_filter_headers(open_frame.get("headers", {})))
    async with local_ws_connect(
        f"ws://127.0.0.1:8080{open_frame['path']}",
        additional_headers=auth_headers,
    ) as local_ws:
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
        done, pending = await asyncio.wait(
            {pull_task, push_task}, return_when=asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()
        # Wait for cancellation to actually be delivered before the `async
        # with` block below closes local_ws out from under a task that may
        # still be mid-recv()/send().
        await asyncio.gather(*pending, return_exceptions=True)

        for t in done:
            exc = t.exception()
            if exc is not None:
                log.error("ws stream %s: pipe task failed", stream_id,
                          exc_info=exc)

    await ws.send(json.dumps({"type": "ws_close", "id": stream_id}))


async def run_tunnel_once(
    tunnel_url: str, tunnel_secret: str,
    ws_connect=websockets.connect,
    http_client: httpx.AsyncClient | None = None,
    local_ws_connect=websockets.connect,
) -> None:
    """Hold one tunnel connection, demultiplexing frames to per-stream
    handlers until the connection drops (raises, per the same
    caller-owns-reconnect contract as signaling_bridge.relay_one_instance).
    """
    client = http_client or _new_http_client()
    stream_queues: dict[str, asyncio.Queue] = {}
    url = f"{tunnel_url}?token={tunnel_secret}"

    # Both _respond_http and _run_ws_stream are dispatched fire-and-forget
    # (so one slow request/stream never blocks the demux loop from reading
    # the next frame) -- but a fire-and-forget asyncio.Task whose outcome is
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
                    if ftype in ("http_request", "ws_open") and stream_id is None:
                        raise KeyError("id")
                except (ValueError, TypeError, KeyError) as exc:
                    log.warning("tunnel: ignoring malformed frame (%s): %.200r",
                                exc.__class__.__name__, raw)
                    continue

                if ftype == "http_request":
                    _dispatch(_respond_http(ws, client, frame))
                elif ftype == "ws_open":
                    queue: asyncio.Queue = asyncio.Queue()
                    stream_queues[stream_id] = queue
                    _dispatch(_run_ws_stream(ws, frame, queue, local_ws_connect))
                elif stream_id in stream_queues:
                    await stream_queues[stream_id].put(frame)
                    if frame.get("type") == "ws_close":
                        stream_queues.pop(stream_id, None)
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
    local_ws_connect=websockets.connect,
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
                    local_ws_connect=local_ws_connect,
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
