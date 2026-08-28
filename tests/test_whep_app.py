import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import asyncio
import time

import pytest
from aiortc import RTCPeerConnection
from fastapi.testclient import TestClient

import server.whep_app as whep_app_module
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


def test_whep_options_preflight_gets_cors_headers(app_and_manager):
    # Critical #2: the browser client's application/sdp POST is not
    # CORS-safelisted, so browsers send an OPTIONS preflight first -- with
    # no CORS middleware, this would 404/405 and the browser would never
    # even attempt the real POST.
    app, webrtc, im = app_and_manager
    client = TestClient(app)
    resp = client.options(
        f"/{im._inst.name}/whep",
        headers={
            "Origin": "http://example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    assert resp.status_code < 300
    assert resp.headers.get("access-control-allow-origin") == "*"


def test_whep_post_response_exposes_location_header_via_cors(app_and_manager):
    # Location is not CORS-safelisted as a *response* header by default --
    # without Access-Control-Expose-Headers, a browser-side client could
    # read the 201 body but not the Location header it needs for its
    # eventual DELETE.
    app, webrtc, im = app_and_manager
    client = TestClient(app)
    resp = client.post(
        f"/{im._inst.name}/whep", content=_make_offer_sdp(),
        headers={"Content-Type": "application/sdp", "Origin": "http://example.com"},
    )
    assert resp.status_code == 201
    expose = resp.headers.get("access-control-expose-headers", "")
    assert "location" in expose.lower()


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


def test_lan_ice_servers_uses_tailscale_stun_not_public(monkeypatch):
    # This process's OWN aiortc RTCPeerConnection must use the
    # Tailscale-bound embedded STUN server (stun_server.py), matching what
    # app.py's /select already hands the browser client as stun_url -- not
    # get_ice_servers()'s public-path list (stun.l.google.com), which
    # reflects the wrong (public/ISP) address for a LAN/Tailscale client and
    # was observed making this process's own RTCPeerConnection try (and
    # fail, 403 Forbidden IP) to allocate a TURN channel through the public
    # coturn instance.
    monkeypatch.setattr(whep_app_module, "STUN_PORT", 3478)
    servers = whep_app_module._lan_ice_servers("100.64.1.2", is_public_path=False)
    assert servers == [{"urls": "stun:100.64.1.2:3478"}]


def test_lan_ice_servers_uses_public_list_untouched_for_public_path(monkeypatch):
    # Public path (signaling_bridge.py's loopback POST) must get
    # get_ice_servers() untouched, not a Tailscale-STUN hybrid: mixing in
    # the Tailscale-bound STUN entry there makes this PC's own host
    # candidate its Tailscale IP (100.64.0.0/10, CGNAT space), and coturn
    # denies relaying to any peer address in that range by default --
    # confirmed live as a second, distinct "403 Forbidden IP" even after
    # gating TURN off for direct LAN clients. Direct LAN/Tailscale peers
    # must never get TURN at all (same live 403).
    monkeypatch.setattr(whep_app_module, "STUN_PORT", 3478)
    monkeypatch.setattr(
        whep_app_module, "get_ice_servers",
        lambda: [
            {"urls": "stun:stun.l.google.com:19302"},
            {"urls": "turn:turn.example.com:3478", "username": "u", "credential": "p"},
        ],
    )
    public_servers = whep_app_module._lan_ice_servers("100.64.1.2", is_public_path=True)
    assert public_servers == [
        {"urls": "stun:stun.l.google.com:19302"},
        {"urls": "turn:turn.example.com:3478", "username": "u", "credential": "p"},
    ]

    lan_servers = whep_app_module._lan_ice_servers("100.64.1.2", is_public_path=False)
    assert lan_servers == [{"urls": "stun:100.64.1.2:3478"}]


def test_whep_post_negotiation_failure_reschedules_grace_timer(monkeypatch):
    # Regression test: _cancel_pending_grace() runs before start_video/
    # create_session are confirmed to succeed. Reproduces the realistic
    # leak case -- start_video succeeds (so video is now actively running)
    # but negotiation then fails on a malformed offer -- and checks the fix
    # still schedules a fresh grace timer for this now-live, zero-viewer
    # video. Without the fix, nothing would ever call stop_video for it
    # again.
    monkeypatch.setattr(whep_app_module, "_CLOSE_GRACE_SECONDS", 0.05)

    class TrackingInstanceManager:
        def __init__(self):
            self._inst = Instance(
                {"id": "adb:emulator-5554", "title": "t", "ldplayer_index": 0},
                FakeSession(), 100, 200,
            )
            self._inst.session.video_active = False
            self.stop_video_calls = []

        def get_by_name(self, name):
            return self._inst if name == self._inst.name else None

        def start_video(self, name):
            return True  # succeeds -- video is now actively running

        def stop_video(self, name):
            self.stop_video_calls.append(name)

    loop = _get_or_create_event_loop()
    webrtc = WebrtcManager(loop)
    im = TrackingInstanceManager()
    app = whep_app_module.create_whep_app(im, webrtc)

    # raise_server_exceptions=False so create_session's negotiation failure
    # (empty/malformed offer SDP, same technique as
    # test_webrtc_manager.py::test_create_session_cleans_up_peer_connection_on_negotiation_failure)
    # comes back as a real 500 response instead of propagating into the test.
    with TestClient(app, raise_server_exceptions=False) as client:
        resp = client.post(
            f"/{im._inst.name}/whep", content="",
            headers={"Content-Type": "application/sdp"},
        )
        assert resp.status_code == 500
        assert webrtc.viewer_count(im._inst.name) == 0

        # If the failure rescheduled the grace timer, stop_video fires once
        # the (monkeypatched, short) grace period elapses.
        time.sleep(0.3)

    assert im.stop_video_calls == [im._inst.name]


def test_grace_teardown_does_not_clobber_concurrent_restart(monkeypatch):
    """Important #5: a grace-period stop_video() dispatch and a fresh POST's
    start_video() must never run concurrently for the same instance -- the
    real bug being closed is that cancelling the asyncio Task wrapping
    `asyncio.to_thread(stop_video)` (what _cancel_pending_grace used to rely
    on) does NOT stop the underlying thread once it has actually started
    running (concurrent.futures.Future.cancel() is a documented no-op once
    RUNNING) -- so a stale teardown can finish AFTER a fresh viewer already
    registered, silently clearing video_active out from under them:
    viewer_count == 1 but video stopped, permanently black, no recovery
    short of a client reconnect.

    Reproduces it directly: instance_manager.stop_video() is made
    deliberately slow (simulating the real socket/thread teardown work
    taking real wall-clock time), a viewer disconnects (scheduling the
    grace timer), the grace period elapses so its stop_video dispatch is
    genuinely in flight, and a second viewer's POST arrives while that's
    still running. Without the fix (no per-instance lock serializing the
    two), this POST sees video_active still True (stop_video hasn't
    cleared it yet), skips calling start_video, and registers just as the
    stale stop_video finishes and clears state -- ending with video
    stopped despite an active viewer. With the fix, this POST blocks until
    the in-flight stop_video genuinely completes, then correctly calls
    start_video again.
    """
    monkeypatch.setattr(whep_app_module, "_CLOSE_GRACE_SECONDS", 0.05)

    class SlowStoppingSession:
        def __init__(self):
            self.video_active = True
            self.control = FakeControl()

        def start_video_aiortc(self, on_frame):
            return True

        def stop_video_aiortc(self):
            pass

    class TrackingInstanceManager:
        def __init__(self):
            self._session = SlowStoppingSession()
            self._inst = Instance(
                {"id": "adb:emulator-5554", "title": "t", "ldplayer_index": 0},
                self._session, 100, 200,
            )
            self.start_video_calls = []
            self.stop_video_started = []
            self.stop_video_finished = []

        def get_by_name(self, name):
            return self._inst if name == self._inst.name else None

        def start_video(self, name):
            self.start_video_calls.append(time.monotonic())
            self._session.video_active = True
            return True

        def stop_video(self, name):
            self.stop_video_started.append(time.monotonic())
            # Simulate real teardown work (closing sockets/threads) taking
            # real wall-clock time -- long enough for a concurrent POST to
            # arrive and attempt start_video while this is still running.
            time.sleep(0.3)
            self._session.video_active = False
            self.stop_video_finished.append(time.monotonic())

    loop = _get_or_create_event_loop()
    webrtc = WebrtcManager(loop)
    im = TrackingInstanceManager()
    app = whep_app_module.create_whep_app(im, webrtc)

    with TestClient(app) as client:
        # Viewer A connects, then disconnects -- schedules the grace timer
        # via WebrtcManager's on_disconnected callback.
        resp_a = client.post(
            f"/{im._inst.name}/whep", content=_make_offer_sdp(),
            headers={"Content-Type": "application/sdp"},
        )
        assert resp_a.status_code == 201
        location_a = resp_a.headers["location"]

        del_resp = client.delete(location_a)
        assert del_resp.status_code == 200

        # Let the (monkeypatched, short) grace period elapse so the grace
        # task is now inside stop_video()'s 0.3s sleep -- genuinely in
        # flight, not merely scheduled.
        time.sleep(0.15)
        assert im.stop_video_started, "grace teardown should have started stop_video by now"
        assert not im.stop_video_finished, "stop_video should still be mid-flight"

        # Viewer B connects while that stale teardown is still running.
        resp_b = client.post(
            f"/{im._inst.name}/whep", content=_make_offer_sdp(),
            headers={"Content-Type": "application/sdp"},
        )
        assert resp_b.status_code == 201

        # Safety margin: give a stale, un-serialized stop_video thread (the
        # pre-fix bug) time to finish and (wrongly) clobber state, so this
        # test actually fails against that behavior instead of asserting
        # before the clobber would have happened.
        time.sleep(0.3)

    assert im.start_video_calls, "start_video must have been called again for viewer B"
    assert im._session.video_active is True
    assert webrtc.viewer_count(im._inst.name) == 1
