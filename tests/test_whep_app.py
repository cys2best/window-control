import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import asyncio
import pytest
from aiortc import RTCPeerConnection
from fastapi.testclient import TestClient

from server.whep_app import create_whep_app
from server.webrtc_manager import WebrtcManager
from server.instance_manager import Instance


class FakeControl:
    def request_idr(self):
        pass


class FakeSession:
    alive = True
    video_active = True
    control = FakeControl()

    def start_video_aiortc(self, on_frame):
        return True

    def stop_video_aiortc(self):
        pass


def _get_or_create_event_loop() -> asyncio.AbstractEventLoop:
    # Plain asyncio.get_event_loop() raises once another test file's
    # pytest-asyncio run has already called set_event_loop(None) at
    # teardown -- harmless when this module runs alone, but this suite runs
    # after test_webrtc_manager.py's async tests do exactly that.
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


@pytest.fixture
def app_and_manager():
    class FakeInstanceManager:
        def __init__(self):
            self._inst = Instance(
                {"id": "adb:emulator-5554", "title": "t", "ldplayer_index": 0},
                FakeSession(), 100, 200,
            )

        def get_by_name(self, name):
            return self._inst if name == self._inst.name else None

        def start_video(self, name):
            return True

        def stop_video(self, name):
            pass

    loop = _get_or_create_event_loop()
    webrtc = WebrtcManager(loop)
    im = FakeInstanceManager()
    app = create_whep_app(im, webrtc)
    return app, webrtc, im


def _make_offer_sdp() -> str:
    async def _build():
        pc = RTCPeerConnection()
        pc.addTransceiver("video", direction="recvonly")
        offer = await pc.createOffer()
        await pc.setLocalDescription(offer)
        sdp = pc.localDescription.sdp
        await pc.close()
        return sdp
    return _get_or_create_event_loop().run_until_complete(_build())


def test_whep_post_returns_answer_and_location(app_and_manager):
    app, webrtc, im = app_and_manager
    client = TestClient(app)
    offer_sdp = _make_offer_sdp()

    resp = client.post(
        f"/{im._inst.name}/whep", content=offer_sdp,
        headers={"Content-Type": "application/sdp"},
    )

    assert resp.status_code == 201
    assert resp.headers["content-type"].startswith("application/sdp")
    assert "m=video" in resp.text
    location = resp.headers["location"]
    assert location.startswith(f"/{im._inst.name}/whep/")
    assert webrtc.viewer_count(im._inst.name) == 1


def test_whep_post_unknown_instance_404(app_and_manager):
    app, webrtc, im = app_and_manager
    client = TestClient(app)
    resp = client.post(
        "/does-not-exist/whep", content=_make_offer_sdp(),
        headers={"Content-Type": "application/sdp"},
    )
    assert resp.status_code == 404


def test_whep_delete_closes_session(app_and_manager):
    app, webrtc, im = app_and_manager
    # Wrapped in `with` so both requests share one portal/event loop -- the
    # RTCPeerConnection the POST creates is bound to that loop, and must
    # still be alive when the DELETE later closes it. This mirrors the real
    # app, which runs its whole life on one event loop under uvicorn; a bare
    # TestClient() spins up a fresh throwaway loop per call instead.
    with TestClient(app) as client:
        resp = client.post(
            f"/{im._inst.name}/whep", content=_make_offer_sdp(),
            headers={"Content-Type": "application/sdp"},
        )
        location = resp.headers["location"]

        del_resp = client.delete(location)

    assert del_resp.status_code == 200
    assert webrtc.viewer_count(im._inst.name) == 0
