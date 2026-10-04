"""Local real broker fixture: no permissive authentication mocks."""
import asyncio
from contextlib import asynccontextmanager
import socket
import threading

import httpx
import uvicorn
from broker.app import BrokerSettings, create_broker_app


@asynccontextmanager
async def local_broker(tmp_path):
    app = create_broker_app(BrokerSettings(storage_path=str(tmp_path / "broker.json"), allowed_origins=["https://phone.example"], turn_shared_secret="test-only", max_active_streams=2))
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", lifespan="off"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(.01)
        assert server.started
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as http:
            yield f"http://127.0.0.1:{port}", http, app
    finally:
        server.should_exit = True
        await asyncio.to_thread(thread.join, 3)
        sock.close()
        assert not thread.is_alive()
