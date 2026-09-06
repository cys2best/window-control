# tests/test_launcher_widget.py
import os
import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_launcher_window_constructs_without_open_app(qapp):
    # Test that LauncherWindow does not require on_open_app and does not have _open_app_btn or _qr_label
    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow()
        assert not hasattr(window, "_open_app_btn")
        assert not hasattr(window, "_qr_label")
        assert hasattr(window, "_status_label")
        assert hasattr(window, "_ip_label")
        assert window.windowTitle().startswith("WindowControl Host")


def test_launcher_window_dimensions_and_close_event(qapp):
    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow()
        # Option B layout is ~400px width, ~460px height
        assert 380 <= window.width() <= 420
        assert 440 <= window.height() <= 480

        # Close event should ignore event and hide window (minimize to tray)
        event = MagicMock()
        window.show()
        assert window.isVisible()
        window.closeEvent(event)
        event.ignore.assert_called_once()
        assert window.isHidden()


def test_launcher_window_status_card_lan_mode(qapp, monkeypatch):
    monkeypatch.setattr("gui.launcher.SUPABASE_URL", None)
    monkeypatch.setattr("gui.launcher.VPS_SIGNALING_URL", None)
    monkeypatch.setattr("gui.launcher.detect_local_ip", lambda: "192.168.1.50")
    monkeypatch.setattr("gui.launcher.detect_tailscale_ip", lambda: None)
    monkeypatch.setattr("gui.launcher.has_tailscale", lambda: False)

    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow()
        assert hasattr(window, "_account_label")
        assert "Auth disabled (LAN mode)" in window._account_label.text()
        assert hasattr(window, "_relay_label")
        assert hasattr(window, "_streams_label")
        assert "192.168.1.50" in window._ip_label.text()


def test_launcher_window_status_card_account_and_tailscale(qapp, monkeypatch):
    monkeypatch.setattr("gui.launcher.SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setattr("gui.launcher.VPS_SIGNALING_URL", "wss://relay.example.com/ws")
    monkeypatch.setattr("gui.launcher.detect_local_ip", lambda: "192.168.1.50")
    monkeypatch.setattr("gui.launcher.detect_tailscale_ip", lambda: "100.80.90.100")
    monkeypatch.setattr("gui.launcher.has_tailscale", lambda: True)

    with patch("gui.launcher.check_for_update"), \
         patch("gui.supabase_login.load_cached_session", return_value={"user": {"email": "host@test.com"}}):
        from gui.launcher import LauncherWindow
        window = LauncherWindow()
        assert "host@test.com" in window._account_label.text()
        assert "100.80.90.100" in window._ip_label.text()
        assert "192.168.1.50" in window._ip_label.text()


def test_launcher_window_active_streams_update(qapp):
    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow()
        window.update_active_streams(0)
        assert "Idle" in window._streams_label.text()
        window.update_active_streams(1)
        assert "1 client streaming" in window._streams_label.text()
        window.update_active_streams(3)
        assert "3 clients streaming" in window._streams_label.text()


def test_launcher_window_action_buttons(qapp):
    stop_called = []
    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow(on_stop_server=lambda: stop_called.append(True))
        assert hasattr(window, "_minimize_btn")
        assert hasattr(window, "_stop_btn")

        window.show()
        assert window.isVisible()
        window._minimize_btn.click()
        assert window.isHidden()

        window._stop_btn.click()
        assert stop_called == [True]
