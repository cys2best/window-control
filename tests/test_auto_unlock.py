# tests/test_auto_unlock.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from service.auto_unlock import CREDENTIAL_SERVICE, get_stored_password, store_password

def test_credential_service_name():
    assert CREDENTIAL_SERVICE == "WindowControl"

def test_store_and_get_password():
    # Uses real keyring on Windows, memory keyring stub on Mac/Linux
    store_password("test_pass_123")
    result = get_stored_password()
    assert result == "test_pass_123"

def test_get_password_returns_none_if_not_set():
    # Clear and verify
    import keyring
    try:
        keyring.delete_password(CREDENTIAL_SERVICE, "unlock")
    except Exception:
        pass
    result = get_stored_password()
    assert result is None
