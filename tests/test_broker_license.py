import hashlib
import json
import re

import pytest

from broker.license_store import (LicenseStore, LicenseStoreError, normalize_key,
                                  generate_key, MOVE_COOLDOWN_SECONDS)

A, B = "a" * 32, "b" * 32
alive = lambda _id: True


def test_generated_key_normalizes_from_sloppy_input():
    key = generate_key()
    assert re.fullmatch(r"EMU(-[0-9A-HJKMNP-TV-Z]{5}){4}", key)
    sloppy = " " + key.lower().replace("0", "o").replace("1", "l") + " "
    assert normalize_key(sloppy) == normalize_key(key)
    assert len(normalize_key(key)) == 20
    with pytest.raises(ValueError):
        normalize_key("EMU-ABCDE")
    with pytest.raises(ValueError):
        normalize_key("EMU-UUUUU-UUUUU-UUUUU-UUUUU")


def test_first_activation_binds_without_cooldown(tmp_path):
    store = LicenseStore(tmp_path / "licenses.json")
    key = store.issue(now=1000)
    assert store.activate(key, A, now=1000, installation_exists=alive).status == "bound"
    assert store.is_licensed(A, now=1001) and not store.is_licensed(B, now=1001)
    moved = store.activate(key, B, now=1002, installation_exists=alive)
    assert (moved.status, moved.previous_installation) == ("moved", A)
    assert store.is_licensed(B, now=1003) and not store.is_licensed(A, now=1003)


def test_second_move_within_cooldown_is_refused(tmp_path):
    store = LicenseStore(tmp_path / "licenses.json")
    key = store.issue(now=0)
    store.activate(key, A, now=0, installation_exists=alive)
    store.activate(key, B, now=10, installation_exists=alive)
    refused = store.activate(key, A, now=20, installation_exists=alive)
    assert (refused.status, refused.retry_after) == ("in_use", MOVE_COOLDOWN_SECONDS - 10)
    assert store.is_licensed(B, now=20)
    assert store.activate(key, A, now=10 + MOVE_COOLDOWN_SECONDS, installation_exists=alive).status == "moved"


def test_reactivating_same_installation_is_idempotent(tmp_path):
    store = LicenseStore(tmp_path / "licenses.json")
    key = store.issue(now=0)
    store.activate(key, A, now=0, installation_exists=alive)
    store.activate(key, B, now=10, installation_exists=alive)
    again = store.activate(key, B, now=20, installation_exists=alive)
    assert again.status == "bound"
    assert store.list()[0].moved_at == 10
    assert store.is_licensed(B, now=21)


def test_binding_to_deleted_installation_rebinds_without_cooldown(tmp_path):
    store = LicenseStore(tmp_path / "licenses.json")
    key = store.issue(now=0)
    store.activate(key, A, now=0, installation_exists=alive)
    store.activate(key, B, now=10, installation_exists=alive)
    result = store.activate(key, A, now=11, installation_exists=lambda i: i != B)
    assert result.status == "bound"
    assert store.is_licensed(A, now=12)


def test_deactivate_clears_binding_and_cooldown(tmp_path):
    store = LicenseStore(tmp_path / "licenses.json")
    key = store.issue(now=0)
    store.activate(key, A, now=0, installation_exists=alive)
    store.activate(key, B, now=10, installation_exists=alive)
    assert store.deactivate(B) is True
    assert not store.is_licensed(B, now=11)
    assert store.deactivate(B) is False
    assert store.activate(key, A, now=12, installation_exists=alive).status == "bound"


def test_unknown_revoked_and_expired_keys_are_invalid(tmp_path):
    store = LicenseStore(tmp_path / "licenses.json")
    assert store.activate(generate_key(), A, now=0, installation_exists=alive).status == "invalid"
    assert store.activate("EMU-ABCDE", A, now=0, installation_exists=alive).status == "invalid"
    revoked = store.issue(now=0)
    store.activate(revoked, A, now=0, installation_exists=alive)
    assert store.revoke(revoked) is True
    assert not store.is_licensed(A, now=1)
    assert store.activate(revoked, A, now=1, installation_exists=alive).status == "invalid"
    expiring = store.issue(now=0, days=1)
    assert store.activate(expiring, B, now=100, installation_exists=alive).status == "bound"
    assert store.is_licensed(B, now=86399)
    assert store.is_licensed(B, now=86400) is False
    assert store.activate(expiring, B, now=86400, installation_exists=alive).status == "invalid"


def test_reset_unbinds_without_cooldown(tmp_path):
    store = LicenseStore(tmp_path / "licenses.json")
    key = store.issue(now=0)
    store.activate(key, A, now=0, installation_exists=alive)
    store.activate(key, B, now=10, installation_exists=alive)
    assert store.reset(key) is True
    assert not store.is_licensed(B, now=11)
    assert store.activate(key, A, now=12, installation_exists=alive).status == "bound"
    assert store.reset(generate_key()) is False
    assert store.revoke(generate_key()) is False


def test_file_holds_digests_only(tmp_path):
    store = LicenseStore(tmp_path / "licenses.json")
    key = store.issue(now=0, note="friend")
    text = (tmp_path / "licenses.json").read_text()
    assert normalize_key(key) not in text and hashlib.sha256(normalize_key(key).encode()).hexdigest() in text
    record = next(iter(json.loads(text).values()))
    assert set(record) == {"installation_id", "issued_at", "bound_at", "moved_at",
                           "expires_at", "revoked", "note"}
    view = store.list()[0]
    assert view.digest_prefix == hashlib.sha256(normalize_key(key).encode()).hexdigest()[:8]
    assert view.note == "friend" and view.installation_id is None


def test_corrupt_license_file_fails_closed(tmp_path):
    path = tmp_path / "licenses.json"
    path.write_text("{not json")
    store = LicenseStore(path)
    assert store.is_licensed(A, now=0) is False
    with pytest.raises(LicenseStoreError):
        store.activate(generate_key(), A, now=0, installation_exists=alive)
    with pytest.raises(LicenseStoreError):
        store.issue(now=0)
    assert path.read_text() == "{not json"


def test_invalid_record_shape_fails_closed(tmp_path):
    path = tmp_path / "licenses.json"
    path.write_text(json.dumps({"0" * 64: {"installation_id": A}}))
    store = LicenseStore(path)
    assert store.is_licensed(A, now=0) is False
    with pytest.raises(LicenseStoreError):
        store.list()


def test_second_store_instance_sees_admin_changes(tmp_path):
    path = tmp_path / "licenses.json"
    broker = LicenseStore(path)
    admin = LicenseStore(path)
    key = admin.issue(now=0)
    assert broker.activate(key, A, now=0, installation_exists=alive).status == "bound"
    assert broker.is_licensed(A, now=1)
    admin.revoke(key)
    assert broker.is_licensed(A, now=2) is False
