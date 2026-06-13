import sys
import pytest


@pytest.mark.skipif(sys.platform != "win32", reason="PyQt5 requires display")
def test_window_list_module_importable():
    """Module imports without error (no QApplication needed for import)."""
    import gui.window_list
    assert hasattr(gui.window_list, 'WindowListWidget')


@pytest.mark.skipif(sys.platform != "win32", reason="PyQt5 requires display")
def test_window_list_widget_class():
    """Class exists with expected signal and methods."""
    from gui.window_list import WindowListWidget
    assert hasattr(WindowListWidget, 'window_selected')
    assert hasattr(WindowListWidget, 'refresh')
    assert hasattr(WindowListWidget, 'get_selected')
