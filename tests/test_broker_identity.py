import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, current_thread
import pytest
from broker import identity_store
from broker.identity_store import InstallationStore, InstallationIdentity


def test_installation_registration_and_authentication(tmp_path):
    store_file = tmp_path / "installations.json"
    store = InstallationStore(storage_path=store_file)

    identity = store.register()
    assert len(identity.installation_id) == 32 or len(identity.installation_id) == 36
    assert len(identity.credential) > 20

    # Digest only in storage, no plaintext credential
    assert store_file.exists()
    stored_data = json.loads(store_file.read_text())
    assert identity.installation_id in stored_data
    stored_record = stored_data[identity.installation_id]
    assert identity.credential not in str(stored_record)
    assert "digest" in stored_record

    # Successful authentication
    assert store.authenticate(identity.installation_id, identity.credential) is True

    # Failed authentication with wrong credential
    assert store.authenticate(identity.installation_id, "wrong_credential") is False

    # Failed authentication with unknown installation
    assert store.authenticate("00000000000000000000000000000000", identity.credential) is False


def test_restart_preserves_installation_credentials(tmp_path):
    store_file = tmp_path / "installations.json"
    store1 = InstallationStore(storage_path=store_file)
    id1 = store1.register()

    # Re-instantiate store to simulate broker restart
    store2 = InstallationStore(storage_path=store_file)
    assert store2.authenticate(id1.installation_id, id1.credential) is True
    assert store2.authenticate(id1.installation_id, "bad_secret") is False


def test_revocation_requires_credential_and_removes_registration(tmp_path):
    store_file = tmp_path / "installations.json"
    store = InstallationStore(storage_path=store_file)
    identity = store.register()

    # Revoke with wrong credential fails
    assert store.revoke(identity.installation_id, "wrong_secret") is False
    assert store.authenticate(identity.installation_id, identity.credential) is True

    # Revoke with valid credential succeeds
    assert store.revoke(identity.installation_id, identity.credential) is True
    assert store.authenticate(identity.installation_id, identity.credential) is False

    # Check removed from disk
    stored_data = json.loads(store_file.read_text())
    assert identity.installation_id not in stored_data


def test_parallel_registrations_preserve_every_acknowledged_identity(tmp_path):
    store = InstallationStore(tmp_path / "identities.json")
    barrier = Barrier(8)

    def register(_):
        barrier.wait(timeout=5)
        return store.register()

    with ThreadPoolExecutor(max_workers=8) as pool:
        identities = list(pool.map(register, range(8)))
    restarted = InstallationStore(store.storage_path)
    assert len(json.loads(store.storage_path.read_text())) == 8
    assert all(restarted.authenticate(i.installation_id, i.credential) for i in identities)


def test_parallel_register_and_revoke_preserve_acknowledged_changes(tmp_path):
    store = InstallationStore(tmp_path / "identities.json")
    existing = [store.register() for _ in range(4)]
    barrier = Barrier(8)

    def change(index):
        barrier.wait(timeout=5)
        if index < 4:
            identity = existing[index]
            return store.revoke(identity.installation_id, identity.credential)
        return store.register()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(change, range(8)))
    assert results[:4] == [True] * 4
    restarted = InstallationStore(store.storage_path)
    assert all(not restarted.exists(i.installation_id) for i in existing)
    assert all(restarted.authenticate(i.installation_id, i.credential) for i in results[4:])
    assert len(json.loads(store.storage_path.read_text())) == 4


@pytest.mark.parametrize("first_operation", ["register", "revoke"])
@pytest.mark.parametrize("second_operation", ["register", "authenticate", "exists", "revoke"])
def test_transactions_cannot_read_during_an_uncommitted_write(
    tmp_path, monkeypatch, first_operation, second_operation
):
    store = InstallationStore(tmp_path / "identities.json")
    existing = store.register()
    write_entered, release_write = Event(), Event()
    second_started, second_read = Event(), Event()
    original_write, original_read = store._write_data, store._read_data
    first_thread = None

    def blocked_write(data):
        if current_thread() is first_thread:
            write_entered.set()
            assert release_write.wait(timeout=5)
        return original_write(data)

    def observed_read():
        if current_thread() is not first_thread:
            second_read.set()
        return original_read()

    def first():
        nonlocal first_thread
        first_thread = current_thread()
        if first_operation == "register":
            return store.register()
        return store.revoke(existing.installation_id, existing.credential)

    def second():
        second_started.set()
        operation = getattr(store, second_operation)
        if second_operation == "register":
            return operation()
        if second_operation == "exists":
            return operation(existing.installation_id)
        return operation(existing.installation_id, existing.credential)

    monkeypatch.setattr(store, "_write_data", blocked_write)
    monkeypatch.setattr(store, "_read_data", observed_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_future = pool.submit(first)
        try:
            assert write_entered.wait(timeout=5)
            second_future = pool.submit(second)
            assert second_started.wait(timeout=5)
            assert not second_read.wait(timeout=.1)
        finally:
            release_write.set()
        first_result, second_result = first_future.result(), second_future.result()
    restarted = InstallationStore(store.storage_path)
    if first_operation == "register":
        assert restarted.authenticate(first_result.installation_id, first_result.credential)
    else:
        assert first_result is True
    if second_operation == "register":
        assert restarted.authenticate(second_result.installation_id, second_result.credential)
    else:
        assert second_result is (first_operation == "register")
    assert restarted.exists(existing.installation_id) is (
        first_operation == "register" and second_operation != "revoke"
    )


@pytest.mark.parametrize("corrupt", [
    b'{"truncated":', b'[]', b'null', b'{}\xff',
    json.dumps({"bad-id": {"digest": "a" * 64}}).encode(),
    json.dumps({"A" * 32: {"digest": "a" * 64}}).encode(),
    json.dumps({"a" * 32: {"digest": "A" * 64}}).encode(),
    json.dumps({"a" * 32: {"digest": "short"}}).encode(),
    json.dumps({"a" * 32: {"digest": 123}}).encode(),
    json.dumps({"a" * 32: {"digest": "a" * 64, "credential": "secret"}}).encode(),
    json.dumps({"a" * 32: {}}).encode(),
    json.dumps({"a" * 32: []}).encode(),
])
@pytest.mark.parametrize("operation", ["register", "authenticate", "exists", "revoke"])
def test_invalid_storage_fails_closed_without_replacing_bytes(tmp_path, corrupt, operation):
    path = tmp_path / "identities.json"
    path.write_bytes(corrupt)
    store = InstallationStore(path)
    arguments = () if operation == "register" else ("a" * 32,)
    if operation in {"authenticate", "revoke"}:
        arguments += ("secret",)
    with pytest.raises(RuntimeError, match="installation storage invalid") as failed:
        getattr(store, operation)(*arguments)
    assert isinstance(failed.value, identity_store.InstallationStoreError)
    assert path.read_bytes() == corrupt


def test_unreadable_storage_is_not_treated_as_empty(tmp_path, monkeypatch):
    store = InstallationStore(tmp_path / "identities.json")
    existing = store.register()
    before = store.storage_path.read_bytes()

    def denied_read(*args, **kwargs):
        raise PermissionError("private storage path and credential")

    monkeypatch.setattr(Path, "read_text", denied_read)
    with pytest.raises(RuntimeError, match="installation storage unavailable") as failed:
        store.register()
    assert isinstance(failed.value, identity_store.InstallationStoreError)
    with pytest.raises(RuntimeError, match="installation storage unavailable") as failed:
        store.authenticate(existing.installation_id, existing.credential)
    assert isinstance(failed.value, identity_store.InstallationStoreError)
    assert store.storage_path.read_bytes() == before


@pytest.mark.parametrize("boundary", ["mkdir", "mkstemp", "fsync", "replace"])
def test_write_failure_preserves_acknowledged_identity_and_cleans_own_tempfile(
    tmp_path, monkeypatch, boundary
):
    store = InstallationStore(tmp_path / "identities.json")
    existing = store.register()
    before = store.storage_path.read_bytes()
    unrelated = tmp_path / "unrelated.tmp"
    unrelated.write_text("keep")

    def failure(*args, **kwargs):
        raise OSError("private storage path and credential")

    target = Path if boundary == "mkdir" else (
        identity_store.tempfile if boundary == "mkstemp" else identity_store.os
    )
    monkeypatch.setattr(target, boundary, failure)
    with pytest.raises(RuntimeError, match="installation storage unavailable") as failed:
        store.register()
    assert isinstance(failed.value, identity_store.InstallationStoreError)
    assert store.storage_path.read_bytes() == before
    assert store.authenticate(existing.installation_id, existing.credential)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["identities.json", "unrelated.tmp"]


def test_persisted_digest_document_is_flushed_and_synced_before_replacement(tmp_path, monkeypatch):
    store = InstallationStore(tmp_path / "identities.json")
    original_sync, original_replace = identity_store.os.fsync, identity_store.os.replace
    synced = []

    def sync(fd):
        # Read the real descriptor before fsync: buffered JSON must already be flushed.
        identity_store.os.lseek(fd, 0, identity_store.os.SEEK_SET)
        synced.append(json.loads(identity_store.os.read(fd, 4096)))
        original_sync(fd)

    def replace(source, destination):
        assert synced == [json.loads(Path(source).read_text())]
        original_replace(source, destination)

    monkeypatch.setattr(identity_store.os, "fsync", sync)
    monkeypatch.setattr(identity_store.os, "replace", replace)
    identity = store.register()
    persisted = json.loads(store.storage_path.read_text())
    assert set(persisted) == {identity.installation_id}
    assert set(persisted[identity.installation_id]) == {"digest"}
    assert len(persisted[identity.installation_id]["digest"]) == 64
    assert identity.credential not in store.storage_path.read_text()
