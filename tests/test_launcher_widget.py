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
        assert window.windowTitle().startswith("EmuCtrl Host")


def test_launcher_window_dimensions_and_close_event(qapp):
    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow()
        assert 380 <= window.width() <= 420
        assert 540 <= window.height() <= 580

        # Close event should ignore event and hide window (minimize to tray)
        event = MagicMock()
        window.show()
        assert window.isVisible()
        window.closeEvent(event)
        event.ignore.assert_called_once()
        assert window.isHidden()


def test_launcher_window_status_card_shows_lan_and_tailscale(qapp, monkeypatch):
    monkeypatch.setattr("gui.launcher.detect_local_ip", lambda: "192.168.1.50")
    monkeypatch.setattr("gui.launcher.detect_tailscale_ip", lambda: "100.80.90.100")
    monkeypatch.setattr("gui.launcher.has_tailscale", lambda: True)

    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow()
        assert "192.168.1.50" in window._ip_label.text()
        assert "100.80.90.100" in window._ip_label.text()
        assert hasattr(window, "_streams_label")
        for removed in ("_relay_label", "_account_band", "_sign_in_btn", "_sign_out_btn"):
            assert not hasattr(window, removed)


def test_launcher_has_no_login_prompt():
    import gui.launcher as launcher
    assert not hasattr(launcher, "maybe_show_login")


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


def test_pair_device_button_shows_a_code(qapp):
    from server.pairing import PairingStore
    store = PairingStore()
    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow(pairing=store)
        assert window._pair_code_label.text() == ""
        assert window._pair_btn.text() == "Pair device"
        assert store.active_code() is None

        window._pair_btn.click()

        code, _remaining = store.active_code()
        assert f"{code[:3]} {code[3:]}" in window._pair_code_label.text()
        assert window._pair_btn.text() == "New code"


def test_code_clears_and_device_appears_once_the_code_is_used(qapp):
    from server.pairing import PairingStore
    store = PairingStore()
    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow(pairing=store)
        window._pair_btn.click()
        store.pair(store.active_code()[0], "Phone")

        window._refresh_pairing()

        assert window._pair_code_label.text() == ""
        assert window._pair_btn.text() == "Pair device"
        assert window._device_list.count() == 1
        assert "Phone" in window._device_list.item(0).text()


def test_paired_devices_can_be_removed_one_at_a_time_or_all(qapp):
    from server.pairing import PairingStore
    store = PairingStore()
    store.pair(store.start_pairing(), "Phone")
    store.pair(store.start_pairing(), "Tablet")
    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow(pairing=store)
        assert window._device_list.count() == 2

        window._device_list.setCurrentRow(0)
        window._remove_device_btn.click()
        assert [d.name for d in store.list_devices()] == ["Tablet"]
        assert window._device_list.count() == 1

        window._unpair_all_btn.click()
        assert store.list_devices() == []
        assert window._device_list.count() == 0
        assert not window._unpair_all_btn.isEnabled()


def test_remove_with_nothing_selected_is_a_no_op(qapp):
    from server.pairing import PairingStore
    store = PairingStore()
    store.pair(store.start_pairing(), "Phone")
    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow(pairing=store)
        window._device_list.setCurrentRow(-1)
        window._remove_device_btn.click()
        assert len(store.list_devices()) == 1


def test_removal_that_cannot_be_saved_warns_the_user(qapp, tmp_path):
    from server.pairing import PairingStore
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    store = PairingStore(str(blocker / "paired_devices.json"))
    store.pair(store.start_pairing(), "Phone")
    store.pair(store.start_pairing(), "Tablet")
    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow(pairing=store)
        window.show()
        assert window._save_warning_label.isHidden()

        window._device_list.setCurrentRow(0)
        window._remove_device_btn.click()
        assert not window._save_warning_label.isHidden()
        assert "return after restart" in window._save_warning_label.text()

        window._unpair_all_btn.click()
        assert "return after restart" in window._save_warning_label.text()


def test_removal_that_is_saved_shows_no_warning(qapp, tmp_path):
    from server.pairing import PairingStore
    store = PairingStore(str(tmp_path / "paired_devices.json"))
    store.pair(store.start_pairing(), "Phone")
    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow(pairing=store)
        window.show()
        window._unpair_all_btn.click()
        assert window._save_warning_label.isHidden()


def _content_height(window):
    return window.centralWidget().sizeHint().height()


def test_window_is_tall_enough_for_its_content(qapp):
    from server.pairing import PairingStore
    store = PairingStore()
    store.pair(store.start_pairing(), "Phone")
    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow(pairing=store)
        assert window.height() >= _content_height(window)


def test_window_grows_when_the_update_banner_and_save_warning_appear(qapp, tmp_path):
    from server.pairing import PairingStore
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory is needed")
    store = PairingStore(str(blocker / "paired_devices.json"))
    store.pair(store.start_pairing(), "Phone")
    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow(pairing=store)
        before = window.height()

        window._on_update_available("9.9.9")
        window._unpair_all()
        qapp.processEvents()

        assert window._save_warning_label.isVisibleTo(window)
        assert window.height() > before
        assert window.height() >= _content_height(window)
        assert window.width() == 400
