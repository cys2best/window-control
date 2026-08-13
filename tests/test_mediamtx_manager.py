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


def test_generate_config_active_path():
    from config import MEDIAMTX_PORT
    cfg = _generate_config(["instance0", "instance1"], active_source="instance1")
    assert "active:" in cfg
    assert f"rtsp://localhost:{MEDIAMTX_PORT}/instance1" in cfg


def test_generate_config_no_active_when_none():
    cfg = _generate_config(["instance0"])
    assert "\n  active:" not in cfg


def test_generate_config_api_enabled():
    cfg = _generate_config(["instance0"])
    assert "api: yes" in cfg
    assert "apiAddress: 127.0.0.1:9997" in cfg


def test_set_active_source_records_current():
    m = MediamtxManager()
    m.set_active_source("instance0")   # no process running → should not raise
    assert m._active_source == "instance0"


def test_active_source_initially_none():
    m = MediamtxManager()
    assert m._active_source is None


def test_set_active_source_not_running_makes_no_network_call():
    # When not running, set_active_source must return before any network I/O.
    import server.mediamtx_manager as mm
    called = {"urlopen": False}
    orig = mm.__dict__.get("urllib", None)
    m = MediamtxManager()
    # Patch urllib.request.urlopen defensively via sys.modules if imported lazily.
    import urllib.request
    real_urlopen = urllib.request.urlopen

    def fake_urlopen(*a, **k):
        called["urlopen"] = True
        raise AssertionError("network call made while not running")

    urllib.request.urlopen = fake_urlopen
    try:
        m.set_active_source("instance1")
    finally:
        urllib.request.urlopen = real_urlopen
    assert called["urlopen"] is False
    assert m._active_source == "instance1"


def test_start_sets_active_source_state():
    # start() should record active_source even if the mediamtx exe is absent
    # (Popen may fail, but _active_source is set before spawn).
    m = MediamtxManager()
    m.start(["instance0", "instance1"], active_source="instance0")
    assert m._active_source == "instance0"


def test_start_falls_back_to_current_active_source_when_none():
    # A restart with active_source=None must NOT blank a live active path:
    # start() falls back to the manager's current _active_source.
    m = MediamtxManager()
    m._active_source = "instance0"
    m.start(["instance0", "instance1"], active_source=None)
    assert m._active_source == "instance0"
