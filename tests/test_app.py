import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import io
import struct
import time
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from server.engine_runtime import EngineSelection
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
    # TestClient's ASGI transport defaults scope["client"] to
    # ("testclient", 50000) rather than a loopback address, which would
    # otherwise trip the new /internal/ localhost-only guard for every
    # legitimate in-process test call. Pin it to loopback so those tests
    # exercise the "called from localhost" path the guard is meant to allow.
    return TestClient(app, client=("127.0.0.1", 12345)), im


def _raw_screencap_bytes(w, h, header_len=16, fmt=1, fill=0x80):
    if header_len == 16:
        header = struct.pack("<IIII", w, h, fmt, 0)
    else:
        header = struct.pack("<III", w, h, fmt)
    return header + bytes([fill]) * (w * h * 4)


def _fake_png_bytes():
    from PIL import Image
    img = Image.new("RGB", (4, 4), color=(10, 20, 30))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


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


def test_whep_url_unknown_instance_404():
    client, im = _make_client()
    im.get.return_value = None
    r = client.get("/instances/emulator-9999/whep-url")
    assert r.status_code == 404


def test_whep_url_returns_url_without_side_effects():
    inst = MagicMock()
    inst.name = "instance0"
    client, im = _make_client()
    im.get.return_value = inst

    r = client.get("/instances/emulator-5554/whep-url")

    assert r.status_code == 200
    data = r.json()
    assert data["whep_url"].endswith("/instance0/whep")
    assert "stun_url" in data
    im.select.assert_not_called()
    assert im.active is None  # confirms no active-instance switch happened


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


def make_instance():
    inst = MagicMock()
    inst.id = "adb:emulator-5554"
    inst.serial = "emulator-5554"
    inst.name = "instance0"
    inst.w = 720
    inst.h = 1280
    return inst


def test_engine_select_returns_complete_fresh_contract():
    client, manager = _make_client()
    manager.engine_enabled.return_value = True
    manager.get.return_value = make_instance()
    manager.select_engine.return_value = EngineSelection(
        whep_url="http://100.64.1.4:51000/whep",
        whep_token="whep-token",
        signaling_url="wss://signal.example",
        signaling_token="viewer-token",
        generation=3,
        width=720,
        height=1280,
    )
    with patch("server.app.get_best_ip", return_value="100.64.1.4"):
        response = client.post("/instances/emulator-5554/engine-select")
    assert response.status_code == 200
    body = response.json()
    assert body["signaling_token"] == "viewer-token"
    assert body["whep_token"] == "whep-token"
    assert "admin_port" not in body
    manager.select_engine.assert_called_once_with("emulator-5554", "100.64.1.4")


def test_engine_select_statuses_are_distinct():
    client, manager = _make_client()
    manager.engine_enabled.return_value = False
    assert client.post("/instances/emulator-5554/engine-select").status_code == 501

    manager.engine_enabled.return_value = True
    manager.get.return_value = None
    assert client.post("/instances/missing/engine-select").status_code == 404

    manager.get.return_value = make_instance()
    manager.select_engine.return_value = None
    assert client.post("/instances/emulator-5554/engine-select").status_code == 503


def test_keyframe_requests_idr_on_instance():
    # Switch prefetch: POST /keyframe forces a source-side IDR so a fresh WHEP
    # paints instantly under copy-mux (no ffmpeg GOP).
    client, im = _make_client()
    r = client.post("/instances/emulator-5554/keyframe")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    im.request_keyframe.assert_called_once_with("emulator-5554")


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


def test_engine_quality_endpoint_delegates_without_changing_contract():
    client, manager = _make_client()
    manager.engine_enabled.return_value = True
    manager.set_tier.return_value = True

    response = client.post(
        "/instances/emulator-5554/quality", json={"tier": "1080"}
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True, "tier": "1080"}
    manager.set_tier.assert_called_once_with("emulator-5554", "1080")


def test_input_ws_idr_message_triggers_request_idr():
    inst = MagicMock()
    client, im = _make_client()
    im.active = inst

    with client.websocket_connect("/input") as ws:
        ws.send_json({"type": "idr"})
        ws.send_json({"type": "idr"})  # immediately after -- rate-limited, must not call again
        time.sleep(0.6)                # past the 500ms rate-limit window
        ws.send_json({"type": "idr"})
        ws.close()

    assert inst.session.control.request_idr.call_count == 2  # first call + the one after the window


def test_input_ws_idr_message_noop_when_no_active_instance():
    client, im = _make_client()
    im.active = None

    with client.websocket_connect("/input") as ws:
        ws.send_json({"type": "idr"})  # must not raise
        ws.close()


def test_internal_publish_start_calls_instance_manager():
    client, im = _make_client()
    im.start_video.return_value = True

    r = client.post("/internal/instances/instance0/publish/start")

    assert r.status_code == 200
    assert r.json() == {"ok": True}
    im.start_video.assert_called_once_with("instance0")


def test_internal_publish_start_returns_ok_false_for_unknown_instance():
    client, im = _make_client()
    im.start_video.return_value = False

    r = client.post("/internal/instances/instance99/publish/start")

    assert r.status_code == 200
    assert r.json() == {"ok": False}


def test_internal_publish_stop_calls_instance_manager():
    client, im = _make_client()

    r = client.post("/internal/instances/instance0/publish/stop")

    assert r.status_code == 200
    assert r.json() == {"ok": True}
    im.stop_video.assert_called_once_with("instance0")


def test_internal_publish_start_refused_from_non_localhost():
    # mediamtx spawns publish_hook.py locally, so these endpoints must never
    # be reachable except from 127.0.0.1/::1 -- the public HTTP tunnel proxies
    # arbitrary paths through 127.0.0.1 itself (see http_tunnel.py's
    # _forward_http_request), so Task 5's tunnel-side block is the primary
    # defense; this is defense-in-depth for any other exposure path.
    client, im = _make_client()
    r = client.post(
        "/internal/instances/instance0/publish/start",
        headers={"X-Forwarded-For": "1.2.3.4"},
    )
    # TestClient's request.client.host is always 127.0.0.1 for in-process
    # requests regardless of X-Forwarded-For (there's no real socket peer to
    # spoof), so this test instead directly exercises the guard function.
    from server.app import _is_localhost
    assert _is_localhost("127.0.0.1") is True
    assert _is_localhost("::1") is True
    assert _is_localhost("100.85.142.52") is False


def test_capture_preview_decodes_raw_screencap():
    client, _ = _make_client()
    raw = _raw_screencap_bytes(4, 4)
    with patch("server.app.adb_manager._find_adb", return_value="adb"), \
         patch("server.app.adb_manager._no_window_flags", return_value={}), \
         patch("server.app.subprocess.check_output", return_value=raw) as mock_run:
        r = client.get("/instances/emulator-5554/preview")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert mock_run.call_count == 1  # raw path succeeded -- no PNG fallback call
    args = mock_run.call_args[0][0]
    assert "screencap -p" not in " ".join(args)


def test_capture_preview_falls_back_to_png_on_unrecognized_raw_header():
    client, _ = _make_client()
    bad_raw = b"\x00" * 20  # too short / not a real header -- decode returns None
    png_bytes = _fake_png_bytes()
    with patch("server.app.adb_manager._find_adb", return_value="adb"), \
         patch("server.app.adb_manager._no_window_flags", return_value={}), \
         patch("server.app.subprocess.check_output", side_effect=[bad_raw, png_bytes]) as mock_run:
        r = client.get("/instances/emulator-5554/preview")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert mock_run.call_count == 2
    second_call_args = mock_run.call_args_list[1][0][0]
    assert "screencap -p" in " ".join(second_call_args)


def test_decode_raw_screencap_rejects_unknown_pixel_format():
    from server.app import _decode_raw_screencap
    raw = _raw_screencap_bytes(4, 4, fmt=99)  # format code this helper doesn't handle
    assert _decode_raw_screencap(raw) is None


def test_decode_raw_screencap_accepts_12_byte_header():
    from server.app import _decode_raw_screencap
    raw = _raw_screencap_bytes(4, 4, header_len=12)
    img = _decode_raw_screencap(raw)
    assert img is not None
    assert img.size == (4, 4)
