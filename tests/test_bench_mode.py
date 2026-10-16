from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from config import bench_mode_from_env
from server.app import create_app
from server.pairing import PairingStore

LOOPBACK = ("127.0.0.1", 5000)
LAN = ("192.168.1.50", 5000)
GUEST_HEADERS = {"Host": "10.0.2.2:8080"}  # LDPlayer guest reaching the host


def _make(peer, bench):
    manager = MagicMock()
    manager.list_instances.return_value = []
    manager.active = None
    with patch("server.app.get_best_ip", return_value="127.0.0.1"):
        app = create_app(manager, PairingStore(), bench=bench)
    return TestClient(app, client=peer)


@pytest.fixture
def web_dir(tmp_path):
    (tmp_path / "bench.html").write_text("<html>bench shell</html>", encoding="utf-8")
    (tmp_path / "bench.txt").write_text("rsc", encoding="utf-8")
    with patch("server.app.WEB_BUILD_DIR", str(tmp_path)):
        yield tmp_path


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("0", False), ("true", False), ("", False), (None, False),
])
def test_bench_mode_only_for_exactly_one(value, expected):
    environ = {} if value is None else {"EMUCTRL_BENCH": value}
    assert bench_mode_from_env(environ) is expected


@pytest.mark.parametrize("path", ["/bench", "/bench.txt", "/bench/target.html"])
def test_bench_routes_absent_without_bench_mode(web_dir, path):
    client = _make(LOOPBACK, bench=False)
    assert client.get(path, headers=GUEST_HEADERS).status_code == 404


def test_target_page_served_to_guest_without_token():
    client = _make(LOOPBACK, bench=True)
    response = client.get("/bench/target.html", headers=GUEST_HEADERS)
    assert response.status_code == 200
    assert "pointerdown" in response.text
    assert response.headers["cache-control"] == "no-store"


def test_target_page_is_static_for_unpaired_lan_peer():
    client = _make(LAN, bench=True)
    assert client.get("/bench/target.html").status_code == 200


def test_bench_shell_served_in_bench_mode(web_dir):
    client = _make(LAN, bench=True)
    assert client.get("/bench").text == "<html>bench shell</html>"
    assert client.get("/bench.txt").status_code == 200


def test_bench_mode_keeps_api_routes_paired(web_dir):
    client = _make(LAN, bench=True)
    assert client.get("/instances").status_code == 401


TIMING = {"input_ms": 3.5, "frame_ms": 12.0, "next_frame_ms": 28.5}


def test_target_timings_absent_without_bench_mode():
    client = _make(LOOPBACK, bench=False)
    assert client.post("/bench/target-timings", json=TIMING).status_code == 404
    assert client.get("/bench/target-timings").status_code == 404


def test_target_timings_are_collected_listed_and_cleared():
    client = _make(LAN, bench=True)  # unpaired: the guest page and bench page both post/read
    assert client.post("/bench/target-timings", json=TIMING).status_code == 204
    assert client.get("/bench/target-timings").json() == [TIMING]
    assert client.delete("/bench/target-timings").status_code == 204
    assert client.get("/bench/target-timings").json() == []


@pytest.mark.parametrize("body", [
    {"input_ms": "3", "frame_ms": 1, "next_frame_ms": 2},
    {"input_ms": float("nan"), "frame_ms": 1, "next_frame_ms": 2},
    {"input_ms": -1, "frame_ms": 1, "next_frame_ms": 2},
    {"frame_ms": 1, "next_frame_ms": 2},
])
def test_target_timings_reject_malformed_values(body):
    client = _make(LOOPBACK, bench=True)
    response = client.post("/bench/target-timings",
                           content=__import__("json").dumps(body, allow_nan=True),
                           headers={"Content-Type": "application/json"})
    assert response.status_code == 422
    assert client.get("/bench/target-timings").json() == []


def test_target_timings_keep_only_the_newest_entries():
    client = _make(LOOPBACK, bench=True)
    for i in range(2005):
        client.post("/bench/target-timings", json={**TIMING, "input_ms": float(i)})
    entries = client.get("/bench/target-timings").json()
    assert len(entries) == 2000
    assert entries[-1]["input_ms"] == 2004.0


def test_target_page_reports_its_timings_to_the_host():
    client = _make(LOOPBACK, bench=True)
    page = client.get("/bench/target.html", headers=GUEST_HEADERS).text
    assert '"/bench/target-timings"' in page
    assert "event.timeStamp" in page
