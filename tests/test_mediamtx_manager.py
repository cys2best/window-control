import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from server.mediamtx_manager import MediamtxManager, _generate_config


def test_generate_config_contains_paths():
    cfg = _generate_config(["instance0", "instance1"])
    assert "instance0:" in cfg
    assert "instance1:" in cfg


def test_generate_config_ports():
    from config import MEDIAMTX_PORT, WHEP_PORT
    cfg = _generate_config([])
    assert f":{MEDIAMTX_PORT}" in cfg
    assert f":{WHEP_PORT}" in cfg


def test_whep_url():
    m = MediamtxManager()
    url = m.whep_url("instance0", "100.64.1.1")
    from config import WHEP_PORT
    assert url == f"http://100.64.1.1:{WHEP_PORT}/instance0/whep"


def test_rtsp_url():
    m = MediamtxManager()
    url = m.rtsp_url("instance0")
    from config import MEDIAMTX_PORT
    assert url == f"rtsp://localhost:{MEDIAMTX_PORT}/instance0"


def test_not_running_initially():
    m = MediamtxManager()
    assert not m.running


def test_generate_config_one_path_per_instance():
    # Each instance is its own always-live path; there is no shared 'active' mux.
    cfg = _generate_config(["instance0", "instance1"])
    assert "  instance0:" in cfg
    assert "  instance1:" in cfg


def test_generate_config_has_no_active_path():
    # Option B removed the 'active' mux entirely — regression guard.
    cfg = _generate_config(["instance0", "instance1"])
    assert "active:" not in cfg


def test_generate_config_api_enabled():
    cfg = _generate_config(["instance0"])
    assert "api: yes" in cfg
    assert "apiAddress: 127.0.0.1:9997" in cfg


def test_no_set_active_source_method():
    # The mux-repoint API is gone; switching is a direct WHEP to instanceN.
    m = MediamtxManager()
    assert not hasattr(m, "set_active_source")


def test_start_accepts_instance_paths_without_active_source():
    # start() no longer takes/records an active_source.
    m = MediamtxManager()
    m.start(["instance0", "instance1"])  # exe likely absent; must not raise
    assert not hasattr(m, "_active_source")


class _FakeProc:
    """Stands in for a live subprocess.Popen without spawning one."""
    def poll(self):
        return None  # still running


def test_add_path_no_process_returns_false():
    m = MediamtxManager()
    assert m.add_path("instance0") is False


def test_remove_path_no_process_returns_false():
    m = MediamtxManager()
    assert m.remove_path("instance0") is False


def test_add_path_calls_api_and_skips_full_restart(monkeypatch):
    m = MediamtxManager()
    m._proc = _FakeProc()
    m._last_args = ([], "100.64.1.1")

    calls = []
    monkeypatch.setattr(m, "_api_call", lambda method, path, body: calls.append((method, path)) or True)
    started = []
    monkeypatch.setattr(m, "start", lambda *a, **kw: started.append((a, kw)))

    assert m.add_path("instance0") is True
    assert calls == [("POST", "/v3/config/paths/add/instance0")]
    assert started == []  # no full restart when the API call succeeds
    assert "instance0" in m._live_paths
    assert m._last_args == (["instance0"], "100.64.1.1")


def test_add_path_already_live_is_noop(monkeypatch):
    m = MediamtxManager()
    m._proc = _FakeProc()
    m._live_paths = {"instance0"}
    calls = []
    monkeypatch.setattr(m, "_api_call", lambda method, path, body: calls.append((method, path)) or True)

    assert m.add_path("instance0") is True
    assert calls == []  # already live, no API call needed


def test_add_path_api_failure_falls_back_to_full_restart(monkeypatch):
    m = MediamtxManager()
    m._proc = _FakeProc()
    m._last_args = (["instance0"], "100.64.1.1")
    m._live_paths = {"instance0"}

    monkeypatch.setattr(m, "_api_call", lambda method, path, body: False)
    started = []
    monkeypatch.setattr(m, "start", lambda names, ip: started.append((set(names), ip)))

    assert m.add_path("instance1") is True
    assert started == [({"instance0", "instance1"}, "100.64.1.1")]


def test_remove_path_calls_api_and_updates_live_paths(monkeypatch):
    m = MediamtxManager()
    m._proc = _FakeProc()
    m._live_paths = {"instance0", "instance1"}
    m._last_args = (["instance0", "instance1"], "100.64.1.1")

    calls = []
    monkeypatch.setattr(m, "_api_call", lambda method, path, body: calls.append((method, path)) or True)

    assert m.remove_path("instance0") is True
    assert calls == [("DELETE", "/v3/config/paths/delete/instance0")]
    assert m._live_paths == {"instance1"}
    assert m._last_args == (["instance1"], "100.64.1.1")


def test_remove_path_not_live_is_noop(monkeypatch):
    m = MediamtxManager()
    m._proc = _FakeProc()
    m._live_paths = {"instance1"}
    calls = []
    monkeypatch.setattr(m, "_api_call", lambda method, path, body: calls.append((method, path)) or True)

    assert m.remove_path("instance0") is True
    assert calls == []
