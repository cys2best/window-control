import asyncio

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve

from engine.test.local_signaling_server import LocalSignalingRelay


@pytest.fixture
async def local_relay():
    relay = LocalSignalingRelay()
    async with serve(relay.handle, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        yield f"ws://127.0.0.1:{port}"


@pytest.mark.asyncio
async def test_local_relay_pairs_engine_and_viewer(local_relay):
    async with (
        connect(f"{local_relay}/?session=pair&role=engine") as engine,
        connect(f"{local_relay}/?session=pair&role=viewer") as viewer,
    ):
        await engine.send("offer-sdp")
        assert await asyncio.wait_for(viewer.recv(), timeout=1) == "offer-sdp"

        await viewer.send("answer-sdp")
        assert await asyncio.wait_for(engine.recv(), timeout=1) == "answer-sdp"


@pytest.mark.asyncio
async def test_local_relay_queues_until_other_role_connects(local_relay):
    async with connect(f"{local_relay}/?session=queued&role=engine") as engine:
        await engine.send("early-offer")
        async with connect(f"{local_relay}/?session=queued&role=viewer") as viewer:
            assert await asyncio.wait_for(viewer.recv(), timeout=1) == "early-offer"


@pytest.mark.asyncio
async def test_local_relay_rejects_duplicate_role(local_relay):
    async with connect(f"{local_relay}/?session=duplicate&role=engine"):
        duplicate = await connect(
            f"{local_relay}/?session=duplicate&role=engine"
        )
        await asyncio.wait_for(duplicate.wait_closed(), timeout=1)
        assert duplicate.close_code == 1008
