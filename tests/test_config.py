import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from config import PORT, QUALITY_MAP, DEV_MODE, get_base_path, CLIENT_DIR, ASSETS_DIR

def test_port_default():
    assert PORT == 8080

def test_quality_map():
    assert QUALITY_MAP['low'] == 40
    assert QUALITY_MAP['medium'] == 65
    assert QUALITY_MAP['high'] == 85

def test_dev_mode_on_mac():
    # On Mac (CI), DEV_MODE must be True
    if sys.platform != 'win32':
        assert DEV_MODE is True

def test_base_path_returns_string():
    assert isinstance(get_base_path(), str)

def test_client_dir_is_string():
    assert isinstance(CLIENT_DIR, str)

def test_quality_tiers_shape():
    from config import QUALITY_TIERS, TIER_ORDER, DEFAULT_TIER
    assert TIER_ORDER == ["480", "720", "1080", "1440"]
    assert DEFAULT_TIER == "720"
    for t in TIER_ORDER:
        tier = QUALITY_TIERS[t]
        assert isinstance(tier["max_size"], int)
        assert tier["bit_rate"].endswith("M")
        assert tier["max_fps"] in (30, 60)

def test_quality_tiers_monotonic():
    from config import QUALITY_TIERS, TIER_ORDER
    sizes = [QUALITY_TIERS[t]["max_size"] for t in TIER_ORDER]
    assert sizes == sorted(sizes)


def test_webrtc_backend_defaults_to_mediamtx(monkeypatch):
    monkeypatch.delenv("WEBRTC_BACKEND", raising=False)
    import importlib
    import config
    importlib.reload(config)
    assert config.WEBRTC_BACKEND == "mediamtx"


def test_webrtc_backend_reads_env(monkeypatch):
    monkeypatch.setenv("WEBRTC_BACKEND", "aiortc")
    import importlib
    import config
    importlib.reload(config)
    assert config.WEBRTC_BACKEND == "aiortc"
    monkeypatch.delenv("WEBRTC_BACKEND", raising=False)
    importlib.reload(config)


def test_aiortc_profile_level_id_is_fixed():
    import config
    assert config.AIORTC_PROFILE_LEVEL_ID == "42e01f"
