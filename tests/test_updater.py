import sys
import os
import pytest
from unittest.mock import patch, MagicMock, call
import urllib.request


def test_get_asset_url_returns_expected_url():
    from updater import _get_asset_url
    url = _get_asset_url("1.2.14")
    assert url == "https://github.com/cys2best/window-control/releases/download/v1.2.14/WindowControlInstaller.exe"


def test_download_and_install_calls_urlretrieve_and_spawn(tmp_path):
    """download_and_install downloads to temp path and spawns installer."""
    from updater import download_and_install

    progress_calls = []
    error_calls = []

    with patch("updater.urllib.request.urlretrieve") as mock_retrieve, \
         patch("updater.subprocess.Popen") as mock_popen, \
         patch("updater.tempfile.gettempdir", return_value=str(tmp_path)):

        mock_retrieve.return_value = (str(tmp_path / "WindowControlInstaller.exe"), None)

        import threading
        done = threading.Event()

        def _progress(pct): progress_calls.append(pct)
        def _error(msg): error_calls.append(msg); done.set()

        original_thread = threading.Thread
        def inline_thread(target=None, daemon=None, **kw):
            t = original_thread(target=target, daemon=daemon, **kw)
            t.start()
            return t

        with patch("updater.threading.Thread", side_effect=inline_thread):
            download_and_install("1.2.14", on_progress=_progress, on_error=_error)
            import time; time.sleep(0.2)

        mock_retrieve.assert_called_once()
        call_args = mock_retrieve.call_args[0]
        assert "1.2.14" in call_args[0]
        assert "WindowControlInstaller.exe" in call_args[1]
        mock_popen.assert_called_once()
        popen_args = mock_popen.call_args[0][0]
        assert "/SILENT" in popen_args
        assert "/NORESTART" in popen_args
        assert not error_calls


def test_download_and_install_calls_on_error_on_failure(tmp_path):
    from updater import download_and_install

    error_calls = []

    with patch("updater.urllib.request.urlretrieve", side_effect=Exception("network down")), \
         patch("updater.tempfile.gettempdir", return_value=str(tmp_path)):

        import threading, time
        original_thread = threading.Thread
        def inline_thread(target=None, daemon=None, **kw):
            t = original_thread(target=target, daemon=daemon, **kw)
            t.start()
            return t

        with patch("updater.threading.Thread", side_effect=inline_thread):
            download_and_install("1.2.14", on_progress=lambda p: None, on_error=lambda e: error_calls.append(e))
            time.sleep(0.2)

    assert error_calls
    assert "network down" in error_calls[0]


def test_download_and_install_calls_on_error_on_spawn_failure(tmp_path):
    from updater import download_and_install

    error_calls = []

    with patch("updater.urllib.request.urlretrieve") as mock_retrieve, \
         patch("updater.subprocess.Popen", side_effect=Exception("spawn failed")), \
         patch("updater.tempfile.gettempdir", return_value=str(tmp_path)):

        mock_retrieve.return_value = (str(tmp_path / "WindowControlInstaller.exe"), None)

        import threading, time
        original_thread = threading.Thread
        def inline_thread(target=None, daemon=None, **kw):
            t = original_thread(target=target, daemon=daemon, **kw)
            t.start()
            return t

        with patch("updater.threading.Thread", side_effect=inline_thread):
            download_and_install("1.2.14", on_progress=lambda p: None, on_error=lambda e: error_calls.append(e))
            time.sleep(0.2)

    assert error_calls
    assert "spawn failed" in error_calls[0]
