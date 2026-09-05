# apps/desktop/test_window.py
from unittest.mock import MagicMock, patch
from window import DesktopWindow


def test_show_creates_window_once():
    with patch("window.webview") as mock_webview:
        mock_webview.create_window.return_value = MagicMock()
        w = DesktopWindow("http://127.0.0.1:8000")
        w.show()
        w.show()
        mock_webview.create_window.assert_called_once_with(
            "WindowControl", "http://127.0.0.1:8000", width=1100, height=750
        )
