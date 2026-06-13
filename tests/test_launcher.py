# tests/test_launcher.py
import sys
import pytest

@pytest.mark.skipif(sys.platform != "win32", reason="PyQt5 requires display on Windows")
def test_launcher_importable():
    from gui.launcher import LauncherWindow
    assert hasattr(LauncherWindow, 'server_start_requested')
    assert hasattr(LauncherWindow, 'server_stop_requested')
    assert hasattr(LauncherWindow, 'quality_changed')
    assert hasattr(LauncherWindow, 'window_selected')

def test_launcher_module_exists():
    import importlib.util
    spec = importlib.util.find_spec('gui.launcher')
    assert spec is not None
