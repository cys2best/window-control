# tests/test_auto_unlock.py
import sys, os
from unittest.mock import patch, mock_open
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from service.auto_unlock import get_stored_password, store_password, delete_password

def test_non_windows_returns_none():
    if sys.platform != "win32":
        store_password("test_pass")
        assert get_stored_password() is None

def test_store_and_get_password_mocked():
    fake_encrypted = b"encrypted_blob"
    with patch("service.auto_unlock.sys.platform", "win32"), \
         patch("service.auto_unlock.os.makedirs"), \
         patch("service.auto_unlock._dpapi_encrypt", return_value=fake_encrypted), \
         patch("service.auto_unlock._dpapi_decrypt", return_value=b"secret_123"), \
         patch("builtins.open", mock_open(read_data=fake_encrypted)):
        store_password("secret_123")
        assert get_stored_password() == "secret_123"

def test_delete_password():
    with patch("service.auto_unlock.os.remove") as mock_remove:
        delete_password()
        mock_remove.assert_called_once()
