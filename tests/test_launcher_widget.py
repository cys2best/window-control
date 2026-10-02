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


def _wait_remote_result(qapp, window):
    import time
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        qapp.processEvents()
        if window._remote_link_label.text() or window._remote_error_label.text():
            return
        time.sleep(0.005)
    pytest.fail("Remote invitation worker did not finish")


def test_owner_click_opens_one_local_code_and_remote_link_off_qt_thread(qapp):
    import threading
    from broker.identity_store import InstallationIdentity
    from server.pairing import PairingStore
    from server.remote_pairing import RemotePairing
    store = PairingStore()
    threads = []
    published = []
    def publish(invitation):
        threads.append(threading.current_thread())
        published.append(invitation)
    remote = RemotePairing(store, InstallationIdentity(installation_id="pc", credential="secret"),
                           "https://remote.example", publish=publish)
    remote.set_online(True)
    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow(pairing=store, remote_pairing=remote)
        window._pair_btn.click()
        displayed_code = store.active_code()[0]
        _wait_remote_result(qapp, window)
        assert store.active_code()[0] == displayed_code
        assert displayed_code[:3] + " " + displayed_code[3:] in window._pair_code_label.text()
        assert remote.active_invitation().url in window._remote_link_label.text()
        assert len(published) == 1
        assert threads[0] != threading.current_thread()
        store.pair(displayed_code, "Local phone")
        window._refresh_pairing()
        assert window._remote_link_label.text() == ""


def test_remote_failure_leaves_local_launcher_pairing_usable(qapp):
    from broker.identity_store import InstallationIdentity
    from server.pairing import PairingStore
    from server.remote_pairing import RemotePairing
    store = PairingStore()
    remote = RemotePairing(store, InstallationIdentity(installation_id="pc", credential="secret"),
                           "https://remote.example")
    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow(pairing=store, remote_pairing=remote)
        window._pair_btn.click()
        _wait_remote_result(qapp, window)
        assert "offline" in window._remote_error_label.text().lower()
        assert window._remote_link_label.text() == ""
        assert store.pair(store.active_code()[0], "Local phone")


def test_background_identity_result_can_attach_remote_without_resetting_local_code(qapp):
    import threading
    from broker.identity_store import InstallationIdentity
    from server.pairing import PairingStore
    from server.remote_pairing import RemotePairing
    store = PairingStore()
    remote = RemotePairing(store, InstallationIdentity(installation_id="pc", credential="secret"),
                           "https://remote.example")
    remote.set_online(True)
    with patch("gui.launcher.check_for_update"):
        from gui.launcher import LauncherWindow
        window = LauncherWindow(pairing=store)
        window._pair_btn.click()
        code = store.active_code()[0]
        worker = threading.Thread(target=lambda: window.remote_pairing_changed.emit(None, "Remote registration failed"))
        worker.start()
        worker.join(1)
        assert window._remote_error_label.text() == ""
        qapp.processEvents()
        assert store.active_code()[0] == code
        assert "registration failed" in window._remote_error_label.text()
        window.set_remote_pairing(remote)
        assert window._remote_error_label.text() == ""
        assert store.active_code()[0] == code
        assert remote.active_invitation() is None
        window._pair_btn.click()
        _wait_remote_result(qapp, window)
        assert remote.active_invitation().url in window._remote_link_label.text()
