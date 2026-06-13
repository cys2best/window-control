import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from server.window_manager import WindowInfo, is_system_window, list_windows

def test_system_window_filtered_empty_title():
    assert is_system_window("") is True

def test_system_window_filtered_taskbar():
    assert is_system_window("Taskbar") is True

def test_system_window_not_filtered_chrome():
    assert is_system_window("Google Chrome") is False

def test_list_windows_returns_list():
    windows = list_windows()
    assert isinstance(windows, list)

def test_list_windows_no_system_titles():
    windows = list_windows()
    for w in windows:
        assert w.title not in ("", "Program Manager", "Desktop", "Taskbar")

def test_window_info_has_required_fields():
    windows = list_windows()
    if windows:
        w = windows[0]
        assert hasattr(w, 'hwnd')
        assert hasattr(w, 'title')
        assert hasattr(w, 'icon_b64')
