"""Local auth-free WebSocket relay for the engine's live signaling tests."""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed


@dataclass
class _Session:
    engine: ServerConnection | None = None
    viewer: ServerConnection | None = None
    queue: deque[tuple[str, Any]] = field(default_factory=deque)


class LocalSignalingRelay:
    """Pair one engine and one viewer per session and relay raw messages."""

    def __init__(self) -> None:
        self._sessions: dict[str, _Session] = {}

    async def handle(self, websocket: ServerConnection) -> None:
        query = parse_qs(urlsplit(websocket.request.path).query)
        session_id = query.get("session", [""])[0]
        role = query.get("role", [""])[0]
        if not session_id or role not in {"engine", "viewer"}:
            await websocket.close(
                1008, "session and role (engine|viewer) query params required"
            )
            return

        session = self._sessions.setdefault(session_id, _Session())
        if getattr(session, role) is not None:
            await websocket.close(1008, "role already taken")
            return
        setattr(session, role, websocket)

        queued = deque()
        while session.queue:
            destination, message = session.queue.popleft()
            if destination == role:
                await websocket.send(message)
            else:
                queued.append((destination, message))
        session.queue = queued

        other_role = "viewer" if role == "engine" else "engine"
        try:
            async for message in websocket:
                target = getattr(session, other_role)
                if target is not None:
                    try:
                        await target.send(message)
                        continue
                    except ConnectionClosed:
                        pass
                session.queue.append((other_role, message))
                while len(session.queue) > 10:
                    session.queue.popleft()
        finally:
            if getattr(session, role) is websocket:
                setattr(session, role, None)
            if session.engine is None and session.viewer is None:
                self._sessions.pop(session_id, None)


async def run(host: str, port: int) -> None:
    relay = LocalSignalingRelay()
    async with serve(relay.handle, host, port):
        print(f"Local signaling server listening on ws://{host}:{port}", flush=True)
        print("Auth is disabled; use this server for local tests only.", flush=True)
        await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8443)
    args = parser.parse_args()
    try:
        asyncio.run(run(args.host, args.port))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
