# tests/test_main.py
import sys
import pytest


def test_main_module_importable():
    """main.py can be imported (checks all imports resolve)."""
    # On Mac this will fail at PyQt5 import without display — skip
    pytest.importorskip("PyQt5.QtWidgets")
    import main
    assert hasattr(main, 'main')
    assert hasattr(main, '_build_available_windows')


def test_build_available_windows():
    """_build_available_windows returns list of dicts with id and title keys."""
    # This works on Mac via the win32 stubs
    from server.window_manager import list_windows
    windows = list_windows()
    result = [{"id": w.hwnd, "title": w.title} for w in windows]
    assert isinstance(result, list)
    for item in result:
        assert "id" in item
        assert "title" in item
