import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from fastapi.testclient import TestClient
from server.stream import CaptureState, FrameQueue
from server.app import create_app

@pytest.fixture
def client():
    state = CaptureState()
    fq = FrameQueue()
    available_windows = [
        {"id": 1001, "title": "Chrome", "icon_b64": ""}
    ]
    state.active_hwnd = 1001
    app = create_app(state, fq, available_windows)
    return TestClient(app)

def test_get_windows(client):
    r = client.get("/windows")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert data[0]["title"] == "Chrome"

def test_post_select_valid(client):
    r = client.post("/select", json={"id": 1001})
    assert r.status_code == 200
    assert r.json()["ok"] is True

def test_post_select_invalid(client):
    r = client.post("/select", json={"id": 9999})
    assert r.status_code == 404

def test_post_quality_low(client):
    r = client.post("/quality", json={"quality": "low"})
    assert r.status_code == 200
    assert r.json()["quality"] == "low"

def test_post_quality_invalid(client):
    r = client.post("/quality", json={"quality": "ultra"})
    assert r.status_code == 422
