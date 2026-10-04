import base64
import hashlib
import hmac
import importlib

import pytest


def test_credentials_expire_at_3600_and_do_not_expose_secret():
    issue = importlib.import_module("broker.turn_credentials").issue_turn_credentials
    host = issue("backend-only", "installation", "session", "host", 1000)
    viewer = issue("backend-only", "installation", "session", "viewer", 1000)
    assert host.expires_at == 4600
    assert host.username == "4600:installation:session:host"
    expected = base64.b64encode(hmac.new(b"backend-only", b"4600:installation:session:host", hashlib.sha1).digest()).decode()
    assert host.credential == expected
    assert host.credential != viewer.credential
    assert "backend-only" not in repr(host)
    assert host.renew_after == 3300


@pytest.mark.parametrize("values", [
    ("", "installation", "session", "host", 0),
    ("secret", "bad:installation", "session", "host", 0),
    ("secret", "installation", "session", "unknown", 0),
    ("secret", "installation", "session", "viewer", float("nan")),
])
def test_credential_scope_rejects_ambiguous_or_unavailable_values(values):
    issue = importlib.import_module("broker.turn_credentials").issue_turn_credentials
    with pytest.raises(ValueError):
        issue(*values)
