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
