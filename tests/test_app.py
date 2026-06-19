import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from unittest.mock import MagicMock, patch
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
