import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from unittest.mock import MagicMock, patch

import pytest

from gui.supabase_login import sign_in, save_session, AuthError


@patch("gui.supabase_login.httpx.post")
def test_sign_in_returns_body_on_success(mock_post):
    mock_post.return_value = MagicMock(
        status_code=200, json=lambda: {"access_token": "jwt-123"}
    )
    result = sign_in("https://project.supabase.co", "anon-key", "a@example.com", "pw")
    assert result == {"access_token": "jwt-123"}
    mock_post.assert_called_once()
    called_url = mock_post.call_args[0][0]
    assert called_url == "https://project.supabase.co/auth/v1/token?grant_type=password"


@patch("gui.supabase_login.httpx.post")
def test_sign_in_raises_auth_error_on_rejection(mock_post):
    mock_post.return_value = MagicMock(
        status_code=400, json=lambda: {"error_description": "Invalid login credentials"}
    )
    with pytest.raises(AuthError, match="Invalid login credentials"):
        sign_in("https://project.supabase.co", "anon-key", "a@example.com", "wrong")


@patch("gui.supabase_login.httpx.post", side_effect=Exception("network down"))
def test_sign_in_raises_auth_error_on_network_failure(mock_post):
    with pytest.raises(AuthError):
        sign_in("https://project.supabase.co", "anon-key", "a@example.com", "pw")


@patch("gui.supabase_login.os.makedirs", side_effect=OSError("Permission denied"))
def test_save_session_swallows_os_error(mock_makedirs):
    # Caching the session locally is a "don't prompt again next launch"
    # convenience. A disk/permission failure here must never propagate and
    # block or crash an otherwise-successful sign-in.
    save_session({"access_token": "jwt-123"})  # must not raise
