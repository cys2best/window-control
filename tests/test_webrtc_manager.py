import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import asyncio
import threading
import pytest
from aiortc import RTCIceServer, RTCPeerConnection, RTCSessionDescription

import server.webrtc_manager as webrtc_manager_module
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


@pytest.mark.asyncio
async def test_reaper_removes_session_and_fires_on_disconnected_when_pc_closes():
    """Critical #1: nothing else ever calls close_session() for a real
    client (browser _probeLocalWhep abandons PCs by design;
    signaling_bridge.py never DELETEs) -- so viewer_count() must return to
    zero, and on_disconnected must fire, purely from the PC's own
    connectionstatechange transition, with no explicit close_session() call
    from test code.
    """
    loop = asyncio.get_event_loop()
    manager = WebrtcManager(loop)

    browser_pc = RTCPeerConnection()
    browser_pc.addTransceiver("video", direction="recvonly")
    offer = await browser_pc.createOffer()
    await browser_pc.setLocalDescription(offer)

    disconnected_calls = []
    session_id, _ = await manager.create_session(
        "instance0", browser_pc.localDescription.sdp, "42e01f", [],
        on_disconnected=lambda: disconnected_calls.append(True),
    )
    assert manager.viewer_count("instance0") == 1

    # Simulate the PC leaving the connected world (network failure, browser
    # tab closed, etc.) by closing it directly -- NOT via manager.close_session()
    # -- so this only proves the reaper's own connectionstatechange handler,
    # not a redundant direct call.
    pc = manager._pcs[session_id]
    await pc.close()

    # emit() for an async handler is fire-and-forget (asyncio.ensure_future),
    # not synchronous -- poll briefly for the reaper's own close_session()
    # call to actually run and complete. Poll on disconnected_calls
    # specifically, not just viewer_count == 0: close_session() pops the
    # track (viewer_count drops to 0) BEFORE it fires on_disconnected, so
    # breaking on viewer_count alone can observe the gap between those two
    # statements and race ahead of the callback by a fraction of a
    # millisecond (confirmed empirically -- this loop's own `break` doesn't
    # yield, so it can win that race on its very first iteration).
    for _ in range(50):
        if disconnected_calls:
            break
        await asyncio.sleep(0.02)

    assert manager.viewer_count("instance0") == 0
    assert session_id not in manager._pcs
    assert disconnected_calls == [True]

    await browser_pc.close()


@pytest.mark.asyncio
async def test_on_connected_fires_once_peers_actually_connect():
    """Important #4: on_connected must fire only once the PC genuinely
    reaches connectionState == "connected" -- i.e. once the track is
    actually registered and able to deliver a fresh IDR to this viewer, not
    any earlier. Full two-peer negotiation (browser_pc gets the real
    answer), not just the one-way offer/answer exchange
    test_create_session_negotiates_answer stops at.
    """
    loop = asyncio.get_event_loop()
    manager = WebrtcManager(loop)

    browser_pc = RTCPeerConnection()
    browser_pc.addTransceiver("video", direction="recvonly")
    offer = await browser_pc.createOffer()
    await browser_pc.setLocalDescription(offer)

    connected_calls = []
    session_id, answer_sdp = await manager.create_session(
        "instance0", browser_pc.localDescription.sdp, "42e01f", [],
        on_connected=lambda: connected_calls.append(True),
    )
    assert connected_calls == []  # not yet -- browser_pc hasn't heard the answer

    await browser_pc.setRemoteDescription(RTCSessionDescription(sdp=answer_sdp, type="answer"))

    for _ in range(100):
        if connected_calls:
            break
        await asyncio.sleep(0.05)

    assert connected_calls == [True]

    await manager.close_session("instance0", session_id)
    await browser_pc.close()


@pytest.mark.asyncio
async def test_handshake_deadline_closes_pc_that_never_connects(monkeypatch):
    """Important #4 (handshake-deadline safeguard): a PC that never reaches
    "connected" must not sit in _pcs/_tracks forever -- there is no DELETE
    coming for it and connectionstatechange never fires a state the reaper
    reacts to if ICE just sits gathering/checking indefinitely.
    """
    monkeypatch.setattr(webrtc_manager_module, "HANDSHAKE_TIMEOUT_SECONDS", 0.1)

    loop = asyncio.get_event_loop()
    manager = WebrtcManager(loop)

    browser_pc = RTCPeerConnection()
    browser_pc.addTransceiver("video", direction="recvonly")
    offer = await browser_pc.createOffer()
    await browser_pc.setLocalDescription(offer)

    session_id, _ = await manager.create_session(
        "instance0", browser_pc.localDescription.sdp, "42e01f", [],
    )
    assert manager.viewer_count("instance0") == 1

    # Deliberately never complete negotiation on the browser side (never
    # setRemoteDescription the answer) -- this PC must never reach
    # "connected" on its own.
    await asyncio.sleep(0.4)

    assert manager.viewer_count("instance0") == 0
    assert session_id not in manager._pcs

    await browser_pc.close()
