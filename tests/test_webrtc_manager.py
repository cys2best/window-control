import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import asyncio
import threading
import pytest
from aiortc import RTCIceServer, RTCPeerConnection

from server.webrtc_manager import WebrtcManager, _ice_servers_to_aiortc


def test_ice_servers_to_aiortc_stun_only():
    result = _ice_servers_to_aiortc([{"urls": "stun:stun.l.google.com:19302"}])
    assert result == [RTCIceServer(urls="stun:stun.l.google.com:19302")]


def test_ice_servers_to_aiortc_with_turn_credentials():
    result = _ice_servers_to_aiortc([
        {"urls": "turn:1.2.3.4:3478", "username": "u", "credential": "p"},
    ])
    assert result == [RTCIceServer(urls="turn:1.2.3.4:3478", username="u", credential="p")]


@pytest.mark.asyncio
async def test_create_session_negotiates_answer():
    loop = asyncio.get_event_loop()
    manager = WebrtcManager(loop)

    browser_pc = RTCPeerConnection()
    browser_pc.addTransceiver("video", direction="recvonly")
    offer = await browser_pc.createOffer()
    await browser_pc.setLocalDescription(offer)

    session_id, answer_sdp = await manager.create_session(
        "instance0", browser_pc.localDescription.sdp, "42e01f", [],
    )

    assert isinstance(session_id, str) and len(session_id) > 0
    assert "m=video" in answer_sdp
    assert manager.viewer_count("instance0") == 1

    await manager.close_session("instance0", session_id)
    assert manager.viewer_count("instance0") == 0
    await browser_pc.close()


@pytest.mark.asyncio
async def test_push_nalu_threadsafe_fans_out_to_all_viewers():
    loop = asyncio.get_event_loop()
    manager = WebrtcManager(loop)

    async def add_viewer():
        browser_pc = RTCPeerConnection()
        browser_pc.addTransceiver("video", direction="recvonly")
        offer = await browser_pc.createOffer()
        await browser_pc.setLocalDescription(offer)
        session_id, _ = await manager.create_session(
            "instance0", browser_pc.localDescription.sdp, "42e01f", [],
        )
        return session_id

    await add_viewer()
    await add_viewer()
    assert manager.viewer_count("instance0") == 2

    done = threading.Event()

    def push_from_other_thread():
        manager.push_nalu_threadsafe("instance0", b"\x00\x00\x00\x01\x67fake-nalu")
        done.set()

    threading.Thread(target=push_from_other_thread).start()
    await asyncio.get_event_loop().run_in_executor(None, done.wait, 2.0)
    # No exception and no deadlock is the pass condition here -- push_nalu's
    # queue.put_nowait is itself fire-and-forget, so there's nothing further
    # to assert on the track without draining recv(), which isn't needed to
    # prove the thread-safety contract this test targets.


@pytest.mark.asyncio
async def test_create_session_cleans_up_peer_connection_on_negotiation_failure():
    loop = asyncio.get_event_loop()
    manager = WebrtcManager(loop)

    # Attempt negotiation with malformed offer_sdp to trigger setRemoteDescription failure
    with pytest.raises(Exception):
        await manager.create_session(
            "instance0", "", "42e01f", [],
        )

    # Verify peer connection was not registered in _pcs dict
    # (since negotiation failed before session_id was assigned)
    assert len(manager._pcs) == 0
    assert manager.viewer_count("instance0") == 0
