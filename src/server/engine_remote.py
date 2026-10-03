"""Private capability-authenticated loopback remote-peer interface."""

import asyncio
import json
import math
import re
import uuid
from dataclasses import dataclass, field
from typing import Callable

import httpx

from server.engine_process import EngineInstance

_SDP_LIMIT = 128 * 1024
_RESPONSE_LIMIT = 1024 * 1024
_PEER_ID = re.compile(r"[a-f0-9]{32}\Z")


@dataclass(frozen=True)
class RemoteEngineEndpoint:
    admin_port: int
    capability: str = field(repr=False)
    generation: int
    owner: EngineInstance = field(repr=False)


@dataclass(frozen=True)
class RemotePeerAnswer:
    peer_id: str
    answer: str = field(repr=False)
    generation: int


class EngineRemoteError(RuntimeError):
    """Safe error that never includes request data or engine secrets."""


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


class EngineRemoteClient:
    def __init__(self, http_client: httpx.AsyncClient | None = None):
        self._http = http_client or httpx.AsyncClient(follow_redirects=False, trust_env=False)
        self._owns_http = http_client is None
        self._cleanup_tasks: set[asyncio.Task] = set()
        self._owners: dict[int, EngineInstance] = {}

    def _url(self, endpoint: RemoteEngineEndpoint) -> str:
        if (type(endpoint.admin_port) is not int or not 0 < endpoint.admin_port < 65536
                or type(endpoint.generation) is not int or endpoint.generation < 0
                or not isinstance(endpoint.capability, str) or not endpoint.capability):
            raise EngineRemoteError("invalid engine endpoint")
        if not endpoint.owner.is_running():
            raise EngineRemoteError("engine process exited")
        previous = self._owners.get(endpoint.admin_port)
        if previous is not None and previous is not endpoint.owner and previous.is_running():
            raise EngineRemoteError("engine endpoint owner changed")
        self._owners[endpoint.admin_port] = endpoint.owner
        return f"http://127.0.0.1:{endpoint.admin_port}/admin/remote-peers"

    async def negotiate(self, endpoint: RemoteEngineEndpoint, session_id: str,
                        offer: str, ice_servers: list[dict], deadline: float, *,
                        on_attempt: Callable[[str], None] | None = None) -> RemotePeerAnswer:
        loop = asyncio.get_running_loop()
        remaining = deadline - loop.time()
        if not math.isfinite(remaining) or not 0 < remaining <= 30:
            raise EngineRemoteError("invalid negotiation budget")
        if not isinstance(offer, str) or not offer or len(offer.encode("utf-8")) > _SDP_LIMIT:
            raise EngineRemoteError("invalid offer size")
        url = self._url(endpoint)
        attempt_id = uuid.uuid4().hex
        if on_attempt is not None:
            on_attempt(attempt_id)
        try:
            remaining = deadline - loop.time()
            timeout_ms = int(remaining * 1000)
            if timeout_ms <= 0:
                raise EngineRemoteError("negotiation deadline expired")
            body = {"peer_id": attempt_id, "session_id": session_id,
                    "generation": endpoint.generation, "offer": offer,
                    "ice_servers": ice_servers, "timeout_ms": timeout_ms}
            async with asyncio.timeout_at(deadline):
                async with self._http.stream("POST", url,
                        headers={"Authorization": f"Bearer {endpoint.capability}"},
                        json=body, timeout=remaining, follow_redirects=False) as response:
                    if response.status_code != 201:
                        raise EngineRemoteError("engine negotiation rejected")
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > _RESPONSE_LIMIT:
                            raise EngineRemoteError("engine response exceeds limit")
                        raw.extend(chunk)
                    data = json.loads(raw, object_pairs_hook=_unique_fields)
            if (not isinstance(data, dict) or set(data) != {"peer_id", "answer", "generation"}
                    or data["peer_id"] != attempt_id
                    or type(data["generation"]) is not int
                    or data["generation"] != endpoint.generation
                    or not isinstance(data["answer"], str) or not data["answer"]
                    or len(data["answer"].encode("utf-8")) > _SDP_LIMIT):
                raise EngineRemoteError("invalid engine answer")
            if loop.time() >= deadline or not endpoint.owner.is_running():
                raise EngineRemoteError("engine answer expired")
            return RemotePeerAnswer(attempt_id, data["answer"], data["generation"])
        except (Exception, asyncio.CancelledError) as error:
            # Retain a strong task reference. Repeated caller cancellation may
            # interrupt this await, but never cancels the exact-ID cleanup.
            cleanup = asyncio.create_task(self.close(endpoint, attempt_id))
            self._cleanup_tasks.add(cleanup)
            cleanup.add_done_callback(self._cleanup_finished)
            try:
                await asyncio.shield(cleanup)
            except (Exception, asyncio.CancelledError):
                pass
            if isinstance(error, asyncio.CancelledError):
                raise asyncio.CancelledError from None
            # The coordinator retains on_attempt's ID until close succeeds (or
            # owner exit is proven), even if both HTTP responses disappeared.
            raise EngineRemoteError("engine negotiation failed; cleanup may require retry") from None

    def _cleanup_finished(self, task: asyncio.Task) -> None:
        self._cleanup_tasks.discard(task)
        if not task.cancelled():
            task.exception()  # Observe failure; caller/coordinator retains the attempt.

    async def close(self, endpoint: RemoteEngineEndpoint, peer_id: str) -> None:
        if not isinstance(peer_id, str) or not _PEER_ID.fullmatch(peer_id):
            raise EngineRemoteError("invalid remote peer id")
        # An exited process cannot retain a peer, and its reused port must never
        # receive the old process's capability.
        if not endpoint.owner.is_running():
            return
        url = self._url(endpoint)
        try:
            async with asyncio.timeout(5):
                response = await self._http.delete(f"{url}/{peer_id}",
                    headers={"Authorization": f"Bearer {endpoint.capability}"},
                    timeout=5, follow_redirects=False)
                if response.status_code != 204:
                    raise EngineRemoteError("engine cleanup rejected")
        except Exception:
            raise EngineRemoteError("engine cleanup failed") from None

    async def aclose(self) -> None:
        if self._cleanup_tasks:
            await asyncio.gather(*self._cleanup_tasks, return_exceptions=True)
        if self._owns_http:
            await self._http.aclose()
