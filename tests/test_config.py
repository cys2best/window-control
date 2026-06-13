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
