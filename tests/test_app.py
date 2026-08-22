import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from server.stream import CaptureState, FrameQueue
from server.app import create_app


def _make_client(instances=None):
    state = CaptureState()
    fq = FrameQueue()
    im = MagicMock()
    im.list_instances.return_value = instances or []
    im.active = None
    im.select.return_value = True
    im.refresh.return_value = None
    with patch("server.app.get_best_ip", return_value="127.0.0.1"):
        app = create_app(state, fq, im)
    return TestClient(app), im


def test_get_instances_empty():
    client, _ = _make_client()
    r = client.get("/instances")
    assert r.status_code == 200
    assert r.json() == []


def test_index_cache_busts_static_assets():
    # The installed iOS PWA has no service worker and caches /static/*.js hard,
    # so a client change without a URL change kept serving stale JS (white
    # screen). index must append ?v=<version> to js/css and send no-cache.
    from config import VERSION
    client, _ = _make_client()
    r = client.get("/")
    if r.status_code == 200:
        assert f"app.js?v={VERSION}" in r.text
        assert f"style.css?v={VERSION}" in r.text
        assert "no-cache" in r.headers.get("cache-control", "")


def test_get_instances_with_data():
    instances = [{"id": "adb:emulator-5554", "serial": "emulator-5554",
                  "title": "LDPlayer #0", "name": "instance0",
                  "w": 720, "h": 1280, "active": False}]
    client, _ = _make_client(instances)
    r = client.get("/instances")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["title"] == "LDPlayer #0"


def test_select_instance_not_found():
    client, im = _make_client()
    im.select.return_value = False
    r = client.post("/instances/emulator-5554/select")
    assert r.status_code == 404


def test_select_instance_ok():
    inst = MagicMock()
    inst.serial = "emulator-5554"
    inst.id = "adb:emulator-5554"
    inst.name = "instance0"
    inst.w = 720
    inst.h = 1280
    inst.ldplayer_index = 0
    client, im = _make_client()
    im.select.return_value = True
    im.active = inst
    with patch("server.app.adb_manager") as mock_adb:
        mock_session = MagicMock()
        mock_session.start.return_value = True
        mock_adb.AdbSession.return_value = mock_session
        r = client.post("/instances/emulator-5554/select")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert "whep_url" in data


def test_select_returns_instance_whep():
    # Option B: WHEP goes straight to the instance's own path, not a shared mux.
    inst = MagicMock()
    inst.serial = "emulator-5554"
    inst.id = "adb:emulator-5554"
    inst.name = "instance0"
    inst.w = 720
    inst.h = 1280
    inst.ldplayer_index = 0
    client, im = _make_client()
    im.select.return_value = True
    im.active = inst
    with patch("server.app.adb_manager") as mock_adb:
        mock_session = MagicMock()
        mock_session.start.return_value = True
        mock_adb.AdbSession.return_value = mock_session
        r = client.post("/instances/emulator-5554/select")
    if r.status_code == 200:
        assert r.json()["whep_url"].endswith("/instance0/whep")


def test_select_instance_includes_signaling_url_when_configured():
    inst = MagicMock()
    inst.serial = "emulator-5554"
    inst.id = "adb:emulator-5554"
    inst.name = "instance0"
    inst.w = 720
    inst.h = 1280
    inst.ldplayer_index = 0
    client, im = _make_client()
    im.select.return_value = True
    im.active = inst

    bridge_calls = []

    async def fake_bridge(*args, **kwargs):
        bridge_calls.append(args)

    with patch("config.VPS_SIGNALING_URL", "ws://vps.example.test:8443"), \
         patch("server.app.VPS_SIGNALING_URL", "ws://vps.example.test:8443"), \
         patch("server.app.run_bridge_with_reconnect", fake_bridge), \
         patch("server.app.adb_manager") as mock_adb:
        mock_session = MagicMock()
        mock_session.start.return_value = True
        mock_adb.AdbSession.return_value = mock_session
        r = client.post("/instances/emulator-5554/select")
    assert r.status_code == 200
    assert r.json()["signaling_url"] == "ws://vps.example.test:8443"
    # The bridge's rendezvous key must match the just-selected instance's
    # name -- findings 1-2 (reconnect + client WS close) depend on this.
    assert bridge_calls and bridge_calls[0][0] == "instance0"


def test_select_instance_omits_signaling_url_when_not_configured():
    inst = MagicMock()
    inst.serial = "emulator-5554"
    inst.id = "adb:emulator-5554"
    inst.name = "instance0"
    inst.w = 720
    inst.h = 1280
    inst.ldplayer_index = 0
    client, im = _make_client()
    im.select.return_value = True
    im.active = inst

    with patch("server.app.VPS_SIGNALING_URL", None), \
         patch("server.app.adb_manager") as mock_adb:
        mock_session = MagicMock()
        mock_session.start.return_value = True
        mock_adb.AdbSession.return_value = mock_session
        r = client.post("/instances/emulator-5554/select")
    assert r.status_code == 200
    assert r.json()["signaling_url"] is None


def test_legacy_select_includes_name():
    # Legacy /select must stay in sync with /instances/{id}/select -- both
    # already agree on whep_url/stun_url; "name" was missing here, which
    # breaks any caller reusing the public-path wiring against this endpoint
    # (session=undefined on the VPS signaling relay).
    inst = MagicMock()
    inst.serial = "emulator-5554"
    inst.id = "adb:emulator-5554"
    inst.name = "instance0"
    inst.w = 720
    inst.h = 1280
    inst.ldplayer_index = 0
    client, im = _make_client()
    im.select.return_value = True
    im.active = inst

    with patch("server.app.adb_manager") as mock_adb:
        mock_session = MagicMock()
        mock_session.start.return_value = True
        mock_adb.AdbSession.return_value = mock_session
        r = client.post("/select", json={"id": "adb:emulator-5554"})
    assert r.status_code == 200
    assert r.json()["name"] == "instance0"


def test_keyframe_requests_idr_on_instance():
    # Switch prefetch: POST /keyframe forces a source-side IDR so a fresh WHEP
    # paints instantly under copy-mux (no ffmpeg GOP).
    inst = MagicMock()
    client, im = _make_client()
    im.get.return_value = inst
    r = client.post("/instances/emulator-5554/keyframe")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    inst.session.control.request_idr.assert_called_once()


def test_keyframe_unknown_instance_is_noop_ok():
    # Unknown instance must not error — prefetch is best-effort fire-and-forget.
    client, im = _make_client()
    im.get.return_value = None
    r = client.post("/instances/emulator-9999/keyframe")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_get_windows_alias():
    """GET /windows should return same as /instances."""
    instances = [{"id": "adb:emulator-5554", "serial": "emulator-5554",
                  "title": "LDPlayer #0", "name": "instance0",
                  "w": 720, "h": 1280, "active": False}]
    client, _ = _make_client(instances)
    r = client.get("/windows")
    assert r.status_code == 200
    assert len(r.json()) == 1


def test_post_quality_low():
    client, _ = _make_client()
    r = client.post("/quality", json={"quality": "low"})
    assert r.status_code == 200
    assert r.json()["quality"] == "low"


def test_post_quality_invalid():
    client, _ = _make_client()
    r = client.post("/quality", json={"quality": "ultra"})
    assert r.status_code == 422


def test_quality_endpoint_rejects_bad_tier(client=None):
    if client is None:
        client, _ = _make_client()
    r = client.post("/instances/emulator-5554/quality", json={"tier": "9000"})
    assert r.status_code == 400


def test_preview_uses_rtsp_stream_when_live():
    client, im = _make_client()
    im.get.return_value = MagicMock()
    im.rtsp_url.return_value = "rtsp://localhost:8554/instance0"
    with patch("server.app._capture_preview_via_stream", new=AsyncMock(return_value=b"jpegbytes")) as stream_mock, \
         patch("server.app._capture_preview", new=AsyncMock()) as adb_mock:
        r = client.get("/instances/emulator-5554/preview")
        assert r.status_code == 200
        assert r.content == b"jpegbytes"
        assert r.headers["content-type"] == "image/jpeg"
        stream_mock.assert_awaited_once()
        adb_mock.assert_not_called()


def test_preview_falls_back_to_adb_when_stream_grab_fails():
    client, im = _make_client()
    im.get.return_value = MagicMock()
    im.rtsp_url.return_value = "rtsp://localhost:8554/instance0"
    from fastapi.responses import Response as _Response
    with patch("server.app._capture_preview_via_stream", new=AsyncMock(return_value=None)), \
         patch("server.app._capture_preview", new=AsyncMock(return_value=_Response(content=b"adbjpeg", media_type="image/jpeg"))) as adb_mock:
        r = client.get("/instances/emulator-5554/preview")
        assert r.status_code == 200
        assert r.content == b"adbjpeg"
        adb_mock.assert_awaited_once()


def test_preview_falls_back_to_adb_when_no_live_rtsp():
    client, im = _make_client()
    im.get.return_value = None
    im.rtsp_url.return_value = None
    from fastapi.responses import Response as _Response
    with patch("server.app._capture_preview_via_stream", new=AsyncMock()) as stream_mock, \
         patch("server.app._capture_preview", new=AsyncMock(return_value=_Response(content=b"adbjpeg", media_type="image/jpeg"))) as adb_mock:
        r = client.get("/instances/emulator-5554/preview")
        assert r.status_code == 200
        assert r.content == b"adbjpeg"
        stream_mock.assert_not_called()
        adb_mock.assert_awaited_once()


def test_grab_rtsp_frame_no_ffmpeg_raises():
    from server.app import _grab_rtsp_frame
    with patch("server.scrcpy_session._get_ffmpeg", return_value=None):
        with pytest.raises(RuntimeError):
            _grab_rtsp_frame("rtsp://localhost:8554/instance0")


def test_grab_rtsp_frame_skips_default_probe():
    # ffmpeg's default probe (analyzeduration=5s/probesize=5MB) is pure
    # overhead against an RTSP source whose SDP already declares the codec --
    # measured logs showed ~1.3-2.4s per grab with idr=~0s, isolating the
    # delay to ffmpeg's own startup, not the IDR round-trip. Regression guard
    # so these flags aren't dropped later.
    from server.app import _grab_rtsp_frame
    with patch("server.scrcpy_session._get_ffmpeg", return_value="/usr/bin/ffmpeg"), \
         patch("server.app.subprocess.check_output", return_value=b"jpegbytes") as co:
        data = _grab_rtsp_frame("rtsp://localhost:8554/instance0")
    assert data == b"jpegbytes"
    args = co.call_args[0][0]
    assert "-probesize" in args
    assert args[args.index("-probesize") + 1] == "32k"
    assert "-analyzeduration" in args
    assert args[args.index("-analyzeduration") + 1] == "0"


@pytest.mark.asyncio
async def test_capture_preview_via_stream_requests_idr_then_grabs():
    from server.app import _capture_preview_via_stream
    inst = MagicMock()
    with patch("server.app._grab_rtsp_frame", return_value=b"jpegbytes"):
        data = await _capture_preview_via_stream(inst, "rtsp://localhost:8554/instance0")
    assert data == b"jpegbytes"
    inst.session.control.request_idr.assert_called_once()


@pytest.mark.asyncio
async def test_capture_preview_via_stream_idr_failure_is_best_effort():
    from server.app import _capture_preview_via_stream
    inst = MagicMock()
    inst.session.control.request_idr.side_effect = Exception("not connected")
    with patch("server.app._grab_rtsp_frame", return_value=b"jpegbytes"):
        data = await _capture_preview_via_stream(inst, "rtsp://localhost:8554/instance0")
    assert data == b"jpegbytes"


@pytest.mark.asyncio
async def test_capture_preview_via_stream_grab_failure_returns_none():
    from server.app import _capture_preview_via_stream
    inst = MagicMock()
    with patch("server.app._grab_rtsp_frame", side_effect=Exception("timeout")):
        data = await _capture_preview_via_stream(inst, "rtsp://localhost:8554/instance0")
    assert data is None
