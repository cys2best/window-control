# tests/test_tray.py
import sys
import pytest
from unittest.mock import MagicMock, patch


def test_tray_module_importable():
    import gui.tray
    assert hasattr(gui.tray, 'TrayIcon')


def test_tray_icon_construction():
    """TrayIcon can be constructed with three callables."""
    from gui.tray import TrayIcon
    show = MagicMock()
    stop = MagicMock()
    exit_ = MagicMock()
    # Construction should not fail (no pystray calls yet)
    tray = TrayIcon(on_show=show, on_stop_server=stop, on_exit=exit_)
    assert tray._on_show is show
    assert tray._on_stop_server is stop
    assert tray._on_exit is exit_
    assert tray._icon is None


def test_load_tray_icon_fallback(tmp_path, monkeypatch):
    """Falls back to blue square when tray_icon.png missing."""
    monkeypatch.setattr('gui.tray.ASSETS_DIR', str(tmp_path))
    from gui import tray as tray_mod
    import importlib
    importlib.reload(tray_mod)
    img = tray_mod._load_tray_icon()
    assert img.size == (64, 64)
    assert img.mode == "RGB"


def test_handle_exit_calls_callback():
    """_handle_exit calls on_exit and stops the icon."""
    from gui.tray import TrayIcon
    exit_called = []
    tray = TrayIcon(
        on_show=MagicMock(),
        on_stop_server=MagicMock(),
        on_exit=lambda: exit_called.append(True)
    )
    icon_mock = MagicMock()
    tray._handle_exit(icon_mock, None)
    assert exit_called == [True]
    icon_mock.stop.assert_called_once()
