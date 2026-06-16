import sys
import os
import pytest
from unittest.mock import patch, MagicMock, call
import urllib.request
import threading


def test_get_asset_url_returns_expected_url():
    from updater import _get_asset_url
    url = _get_asset_url("1.2.14")
    assert url == "https://github.com/cys2best/window-control/releases/download/v1.2.14/WindowControlInstaller.exe"


def _make_mock_response(content=b"fake_installer_data", content_length=None):
    """Create a mock urllib response object."""
    mock_resp = MagicMock()
    data = iter([content, b""])
    mock_resp.read.side_effect = lambda _: next(data) if data else b""
    mock_resp.headers = {"Content-Length": str(content_length or len(content))}
    mock_resp.__enter__ = lambda s: s
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def test_download_and_install_calls_urlopen_and_spawn(tmp_path):
    """download_and_install downloads to temp path and spawns installer."""
    from updater import download_and_install

    progress_calls = []
    error_calls = []
    done = threading.Event()

    mock_resp = _make_mock_response(b"x" * 1000, content_length=1000)

    with patch("updater.urllib.request.urlopen", return_value=mock_resp) as mock_open, \
         patch("updater.subprocess.Popen") as mock_popen, \
         patch("updater.tempfile.gettempdir", return_value=str(tmp_path)):

        def _progress(pct):
            progress_calls.append(pct)

        def _error(msg):
            error_calls.append(msg)
            done.set()

        original_thread = threading.Thread
        def tracked_thread(target=None, daemon=None, **kw):
            def wrapped():
                target()
                done.set()
            return original_thread(target=wrapped, daemon=daemon, **kw)

        with patch("updater.threading.Thread", side_effect=tracked_thread):
            download_and_install("1.2.14", on_progress=_progress, on_error=_error)

        done.wait(timeout=5)

        mock_open.assert_called_once()
        call_url = mock_open.call_args[0][0].full_url
        assert "1.2.14" in call_url
        mock_popen.assert_called_once()
        popen_args = mock_popen.call_args[0][0]
        assert "/SILENT" in popen_args
        assert "/NORESTART" in popen_args
        assert not error_calls


def test_download_and_install_calls_on_error_on_failure(tmp_path):
    from updater import download_and_install

    error_calls = []
    done = threading.Event()

    with patch("updater.urllib.request.urlopen", side_effect=Exception("network down")), \
         patch("updater.tempfile.gettempdir", return_value=str(tmp_path)):

        original_thread = threading.Thread
        def tracked_thread(target=None, daemon=None, **kw):
            def wrapped():
                target()
                done.set()
            return original_thread(target=wrapped, daemon=daemon, **kw)

        with patch("updater.threading.Thread", side_effect=tracked_thread):
            download_and_install("1.2.14", on_progress=lambda p: None, on_error=lambda e: (error_calls.append(e), done.set()))

        done.wait(timeout=5)

    assert error_calls
    assert "network down" in error_calls[0]


def test_download_and_install_calls_on_error_on_spawn_failure(tmp_path):
    from updater import download_and_install

    error_calls = []
    done = threading.Event()

    mock_resp = _make_mock_response(b"x" * 100, content_length=100)

    with patch("updater.urllib.request.urlopen", return_value=mock_resp), \
         patch("updater.subprocess.Popen", side_effect=Exception("spawn failed")), \
         patch("updater.tempfile.gettempdir", return_value=str(tmp_path)):

        original_thread = threading.Thread
        def tracked_thread(target=None, daemon=None, **kw):
            def wrapped():
                target()
                done.set()
            return original_thread(target=wrapped, daemon=daemon, **kw)

        with patch("updater.threading.Thread", side_effect=tracked_thread):
            download_and_install("1.2.14", on_progress=lambda p: None, on_error=lambda e: (error_calls.append(e), done.set()))

        done.wait(timeout=5)

    assert error_calls
    assert "spawn failed" in error_calls[0]
