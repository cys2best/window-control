import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import io
import importlib.util
import struct
import time
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from server.engine_runtime import EngineSelection
from server.app import create_app


def _make_client(instances=None):
    im = MagicMock()
    im.list_instances.return_value = instances or []
    im.active = None
    im.select.return_value = EngineSelection(
        whep_url="http://100.64.1.4:51000/whep",
        whep_token="whep-token",
        signaling_url=None,
        public_session=None,
        generation=0,
        width=720,
        height=1280,
    )
    im.refresh.return_value = None
    with patch("server.app.get_best_ip", return_value="127.0.0.1"):
        app = create_app(im)
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


def test_index_serves_apps_web_export_uncached_and_unrewritten(tmp_path):
    # apps/web (Next.js, output: "export") content-hashes its own asset
    # filenames (e.g. main-app-<hash>.js), so unlike the old hand-rolled
    # client, index.html needs no ?v=<VERSION> query-string rewrite to
    # cache-bust -- a content change already changes the hash/URL. This
    # test proves the served HTML is passed through byte-for-byte (no
    # rewrite reintroduced) while still disabling caching on the HTML
    # document itself (the one thing that can go legitimately stale: which
    # hashed bundle it points at).
    import server.app as app_module
    fake_html = '<html><script src="/_next/static/chunks/main-app-abc123.js"></script></html>'
    (tmp_path / "index.html").write_text(fake_html)
    with patch.object(app_module, "WEB_BUILD_DIR", str(tmp_path)):
        client, _ = _make_client()
        r = client.get("/")
    assert r.status_code == 200
    assert r.text == fake_html
    assert "no-cache" in r.headers.get("cache-control", "")


def test_index_missing_build_returns_500():
    import server.app as app_module
    with patch.object(app_module, "WEB_BUILD_DIR", "/nonexistent/path"):
        client, _ = _make_client()
        r = client.get("/")
    assert r.status_code == 500


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
    im.active = inst

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


def test_instance_select_returns_exact_engine_contract():
    client, manager = _make_client()
    manager.get.return_value = make_instance()
    manager.select.return_value = EngineSelection(
        whep_url="http://100.64.1.4:51000/whep",
        whep_token="whep-token",
        signaling_url="wss://signal.example",
        public_session="owner-1.instance0",
        generation=3,
        width=720,
        height=1280,
    )
    with patch("server.app.get_best_ip", return_value="100.64.1.4"), \
         patch("server.app.get_ice_servers", return_value=[
             {"urls": "stun:100.64.1.4:3478"},
             {"urls": "stun:stun.l.google.com:19302"},
         ]):
        response = client.post("/instances/emulator-5554/select")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "ok", "id", "serial", "name", "w", "h", "whep_url",
        "whep_token", "signaling_url", "public_session", "ice_servers",
        "generation",
    }
    assert body["public_session"] == "owner-1.instance0"
    assert body["whep_token"] == "whep-token"
    assert body["ice_servers"] == [
        {"urls": "stun:100.64.1.4:3478"},
        {"urls": "stun:stun.l.google.com:19302"},
    ]
    manager.select.assert_called_once_with("emulator-5554", "100.64.1.4")


def test_instance_select_statuses_are_distinct():
    client, manager = _make_client()
    manager.get.return_value = None
    assert client.post("/instances/missing/select").status_code == 404

    manager.get.return_value = make_instance()
    manager.select.return_value = None
    assert client.post("/instances/emulator-5554/select").status_code == 503


def test_instance_select_nulls_disabled_public_capabilities_together():
    client, manager = _make_client()
    manager.get.return_value = make_instance()
    manager.select.return_value = EngineSelection(
        whep_url="http://100.64.1.4:51000/whep", whep_token="whep-token",
        signaling_url=None, public_session=None, generation=4,
        width=1280, height=720,
    )

    response = client.post("/instances/emulator-5554/select")

    assert response.status_code == 200
    assert response.json()["signaling_url"] is None
    assert response.json()["public_session"] is None


def test_instance_select_formats_ipv6_stun_and_removes_duplicate_urls():
    client, manager = _make_client()
    manager.get.return_value = make_instance()
    manager.select.return_value = EngineSelection(
        whep_url="http://[fd7a:115c:a1e0::1]:51000/whep", whep_token="whep-token",
        signaling_url=None, public_session=None, generation=4,
        width=1280, height=720,
    )
    with patch("server.app.get_best_ip", return_value="fd7a:115c:a1e0::1"), \
         patch("server.app.get_ice_servers", return_value=[
             {"urls": "stun:[fd7a:115c:a1e0::1]:3478"},
             {"urls": ["stun:other", "stun:other"]},
         ]):
        response = client.post("/instances/emulator-5554/select")

    assert response.status_code == 200
    assert response.json()["ice_servers"] == [
        {"urls": "stun:[fd7a:115c:a1e0::1]:3478"},
        {"urls": ["stun:other"]},
    ]


def test_instance_select_mints_fresh_capabilities_each_time():
    client, manager = _make_client()
    manager.get.return_value = make_instance()
    manager.select.side_effect = [
        EngineSelection("http://host/whep", "first", None, None, 1, 1280, 720),
        EngineSelection("http://host/whep", "second", None, None, 1, 1280, 720),
    ]

    first = client.post("/instances/emulator-5554/select")
    second = client.post("/instances/emulator-5554/select")

    assert first.status_code == second.status_code == 200
    assert first.json()["whep_token"] == "first"
    assert second.json()["whep_token"] == "second"
    assert manager.select.call_count == 2


@pytest.mark.parametrize("method,path", [
    ("post", "/instances/emulator-5554/engine-select"),
    ("get", "/instances/emulator-5554/whep-url"),
    ("post", "/internal/instances/instance0/publish/start"),
    ("post", "/input"),
])
def test_removed_engine_and_server_input_routes_are_not_registered(method, path):
    client, _ = _make_client()
    assert getattr(client, method)(path).status_code == 404


def test_legacy_android_mjpeg_routes_are_not_registered():
    client, _ = _make_client()
    registered_paths = {route.path for route in client.app.routes}

    # "/stream" is deliberately excluded from this set: it's now apps/web's
    # stream-view page-shell route (GET, serves stream.html -- see
    # test_stream_route_serves_web_page_not_legacy_mjpeg below), not the
    # removed Android MJPEG multipart endpoint that used to live there.
    assert registered_paths.isdisjoint({"/stats", "/reconnect", "/quality"})


def test_stream_route_serves_web_page_not_legacy_mjpeg(tmp_path):
    # Confirms "/stream" being a registered route again (excluded above)
    # is apps/web's static page shell, not a resurrection of the removed
    # Android MJPEG multipart-response endpoint.
    import server.app as app_module
    (tmp_path / "stream.html").write_text("<html>stream shell</html>")
    with patch.object(app_module, "WEB_BUILD_DIR", str(tmp_path)):
        client, _ = _make_client()
        r = client.get("/stream")
    assert r.status_code == 200
    assert "multipart" not in r.headers.get("content-type", "")
    assert r.text == "<html>stream shell</html>"


def test_instances_serves_the_web_page_shell_to_a_browser_navigation(tmp_path):
    # apps/web's own /instances route is where the app lands after login.
    # A browser hard-navigating/reloading there must get the app shell,
    # not the JSON list -- the JSON API keeps the same path (below).
    import server.app as app_module
    (tmp_path / "instances.html").write_text("<html>instance list shell</html>")
    with patch.object(app_module, "WEB_BUILD_DIR", str(tmp_path)):
        client, _ = _make_client(instances=[{"id": "adb:a", "serial": "a"}])
        r = client.get("/instances", headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.text == "<html>instance list shell</html>"


@pytest.mark.parametrize("headers", [
    {},                                  # packages/core + apps/mobile: plain fetch()
    {"Accept": "*/*"},
    {"Accept": "application/json"},
])
def test_instances_json_contract_is_unchanged_for_api_consumers(headers, tmp_path):
    # packages/core's makeClient() sets no Accept header at all (only
    # Authorization), so its request shape must still get the JSON list
    # even with a page shell sitting on the same path.
    import server.app as app_module
    (tmp_path / "instances.html").write_text("<html>instance list shell</html>")
    with patch.object(app_module, "WEB_BUILD_DIR", str(tmp_path)):
        client, _ = _make_client(instances=[{"id": "adb:a", "serial": "a"}])
        r = client.get("/instances", headers=headers)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.json() == [{"id": "adb:a", "serial": "a"}]


@pytest.mark.parametrize("path,name", [
    ("/instances.txt", "instances.txt"),
    ("/index.txt", "index.txt"),
    ("/login.txt", "login.txt"),
])
def test_rsc_payloads_are_served_so_soft_navigation_does_not_hard_reload(
    path, name, tmp_path
):
    # Next 15's client router fetches "<route>.txt" for every client-side
    # navigation in an export build and falls back to a full page load if
    # it 404s. A 404 here is what made router.replace("/instances") turn
    # into a hard navigation onto the JSON API route.
    import server.app as app_module
    (tmp_path / name).write_bytes(b"0:payload\n")
    with patch.object(app_module, "WEB_BUILD_DIR", str(tmp_path)):
        client, _ = _make_client()
        r = client.get(path)
    assert r.status_code == 200
    assert r.text in ("0:payload\n", "0:payload\r\n")
    # The router only treats a payload as usable when the content type is
    # text/x-component or text/plain; anything else hard-navigates.
    content_type = r.headers["content-type"]
    assert content_type.startswith("text/x-component") or content_type.startswith("text/plain")
    # Fixed filename, new contents every build -- same staleness risk the
    # HTML shells have, so the same no-cache treatment.
    assert "no-cache" in r.headers.get("cache-control", "")


def test_unknown_rsc_payload_is_a_404_not_a_traversal(tmp_path):
    import server.app as app_module
    with patch.object(app_module, "WEB_BUILD_DIR", str(tmp_path)):
        client, _ = _make_client()
        assert client.get("/nope.txt").status_code == 404
        # Backslash is a path separator on Windows; the name filter must
        # reject it before os.path.join ever sees it.
        assert client.get("/..%5C..%5Csecret.txt").status_code == 404


def test_manifest_and_icon_are_served_for_pwa_installability(tmp_path):
    import server.app as app_module
    (tmp_path / "manifest.json").write_text('{"name":"WindowControl"}')
    (tmp_path / "icon-192.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    with patch.object(app_module, "WEB_BUILD_DIR", str(tmp_path)):
        client, _ = _make_client()
        manifest = client.get("/manifest.json")
        icon = client.get("/icon-192.png")
    assert manifest.status_code == 200
    assert manifest.headers["content-type"].startswith("application/manifest+json")
    assert manifest.json() == {"name": "WindowControl"}
    assert icon.status_code == 200
    assert icon.headers["content-type"].startswith("image/png")


def test_android_mjpeg_runtime_is_absent():
    from server import adb_manager

    assert not hasattr(adb_manager, "AdbSession")
    assert not hasattr(adb_manager, "_get_ffmpeg")
    assert importlib.util.find_spec("server.stream") is None


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


def test_capture_preview_unquotes_and_strips_adb_prefix():
    client, _ = _make_client()
    raw = _raw_screencap_bytes(4, 4)
    with patch("server.app.adb_manager._find_adb", return_value="adb"), \
         patch("server.app.adb_manager._no_window_flags", return_value={}), \
         patch("server.app.subprocess.check_output", return_value=raw) as mock_run:
        r = client.get("/instances/adb%3A127.0.0.1%3A5555/preview")
    assert r.status_code == 200
    assert mock_run.call_count == 1
    args = mock_run.call_args[0][0]
    assert "-s" in args
    idx = args.index("-s")
    assert args[idx + 1] == "127.0.0.1:5555"


def test_query_preview_aliases():
    client, manager = _make_client()
    manager.list_instances.return_value = [{"serial": "emulator-5554", "name": "LDPlayer"}]
    raw = _raw_screencap_bytes(4, 4)
    with patch("server.app.adb_manager._find_adb", return_value="adb"), \
         patch("server.app.adb_manager._no_window_flags", return_value={}), \
         patch("server.app.subprocess.check_output", return_value=raw) as mock_run:
        r1 = client.get("/preview?serial=adb:emulator-5554")
        r2 = client.get("/instances/preview")
    assert r1.status_code == 200
    assert r2.status_code == 200

