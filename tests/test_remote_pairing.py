from urllib.parse import urlsplit

import pytest

from broker.identity_store import InstallationIdentity
from remote_protocol import ErrorCode
from server.pairing import PairingStore


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


def manager(tmp_path, *, online=True, publish=None, close=None, on_error=None):
    from server.remote_pairing import RemotePairing
    clock = Clock()
    pairing = PairingStore(str(tmp_path / "devices.json"), clock=clock)
    identity = InstallationIdentity(installation_id="pc-123", credential="owner-secret")
    remote = RemotePairing(pairing, identity, "https://remote.example", publish=publish, close=close,
                           on_error=on_error)
    remote.set_online(online)
    return pairing, remote, clock


def assert_error(remote, handle, code, expected):
    from server.remote_pairing import RemotePairingError
    with pytest.raises(RemotePairingError) as error:
        remote.submit(handle, code, "Phone")
    assert error.value.code == expected


def test_remote_and_local_share_five_attempt_budget(tmp_path):
    closed = []
    local, remote, _ = manager(tmp_path, close=closed.append)
    code = local.start_pairing()
    invitation = remote.open()
    wrong = "111111" if code != "111111" else "222222"
    for _ in range(2):
        assert local.pair(wrong, "Attacker") is None
        assert_error(remote, invitation.handle, wrong, ErrorCode.NOT_PAIRED)
    assert_error(remote, invitation.handle, wrong, ErrorCode.NOT_PAIRED)
    assert local.active_code() is None
    assert remote.active_invitation() is None
    assert closed == [invitation.handle]
    assert_error(remote, invitation.handle, code, ErrorCode.EXPIRED_PAIRING)


def test_rendezvous_is_single_use_and_expires_at_300_seconds(tmp_path):
    local, remote, clock = manager(tmp_path)
    code = local.start_pairing()
    invitation = remote.open()
    assert invitation.expires_at == 1300.0
    clock.now = 1299.0
    result = remote.submit(invitation.handle, code, "Phone")
    assert result["installation_id"] == "pc-123"
    assert local.is_valid_token(result["token"])
    assert result["token"] not in (tmp_path / "devices.json").read_text()
    assert_error(remote, invitation.handle, code, ErrorCode.EXPIRED_PAIRING)
    local.start_pairing()
    invitation = remote.open()
    clock.now = 1599.0
    assert_error(remote, invitation.handle, local.active_code()[0] if local.active_code() else code, ErrorCode.EXPIRED_PAIRING)
    assert remote.active_invitation() is None


def test_code_is_not_in_pairing_url_and_open_never_resets_local_window(tmp_path):
    local, remote, clock = manager(tmp_path)
    code = local.start_pairing()
    clock.now += 50
    invitation = remote.open()
    assert len(invitation.handle) >= 22
    assert invitation.expires_at == 1300.0
    assert local.active_code() == (code, 250)
    assert urlsplit(invitation.url).fragment == "invite=" + invitation.handle
    assert invitation.url == "https://remote.example/pair#invite=" + invitation.handle
    assert code not in invitation.url
    assert "owner-secret" not in invitation.url
    assert remote.open() == invitation


def test_local_consumption_and_replacement_invalidate_remote_immediately(tmp_path):
    closed = []
    local, remote, _ = manager(tmp_path, close=closed.append)
    code = local.start_pairing()
    first = remote.open()
    local.start_pairing()
    assert closed == [first.handle]
    assert_error(remote, first.handle, code, ErrorCode.EXPIRED_PAIRING)
    second = remote.open()
    assert second.handle != first.handle
    local.pair(local.active_code()[0], "Local phone")
    assert closed == [first.handle, second.handle]
    assert remote.active_invitation() is None


def test_offline_host_does_not_accept_pairing(tmp_path):
    from server.remote_pairing import RemotePairingError
    local, remote, _ = manager(tmp_path, online=False)
    code = local.start_pairing()
    with pytest.raises(RemotePairingError) as error:
        remote.open()
    assert error.value.code == ErrorCode.OFFLINE
    remote.set_online(True)
    invitation = remote.open()
    remote.set_online(False)
    assert_error(remote, invitation.handle, code, ErrorCode.OFFLINE)
    assert local.pair(code, "Local phone")


def test_publication_failure_preserves_local_code_and_has_no_invitation(tmp_path):
    from server.remote_pairing import RemotePairingError
    def fail(invitation):
        raise OSError("disconnected")
    local, remote, _ = manager(tmp_path, publish=fail)
    code = local.start_pairing()
    with pytest.raises(RemotePairingError):
        remote.open()
    assert remote.active_invitation() is None
    assert local.pair(code, "Local phone")


def test_wrong_handle_does_not_consume_local_attempts(tmp_path):
    local, remote, _ = manager(tmp_path)
    code = local.start_pairing()
    invitation = remote.open()
    for _ in range(10):
        assert_error(remote, "unknown", "000000", ErrorCode.EXPIRED_PAIRING)
    assert remote.submit(invitation.handle, code, "Phone")["token"]


def test_pairing_store_failure_is_explicit_remote_error_with_local_access(tmp_path):
    from server.remote_pairing import RemotePairingError
    blocker = tmp_path / "devices.json"
    blocker.mkdir()
    local, remote, _ = manager(tmp_path)
    code = local.start_pairing()
    invitation = remote.open()
    with pytest.raises(RemotePairingError) as error:
        remote.submit(invitation.handle, code, "Phone")
    assert error.value.code == ErrorCode.UNAVAILABLE
    assert [device.name for device in local.list_devices()] == ["Phone"]
    assert local.pair(local.start_pairing(), "Local phone")


def test_shutdown_closes_invitation_and_detaches_observer(tmp_path):
    import gc
    import weakref
    local, remote, _ = manager(tmp_path)
    local.start_pairing()
    invitation = remote.open()
    remote.shutdown()
    assert remote.active_invitation() is None
    assert_error(remote, invitation.handle, "000000", ErrorCode.OFFLINE)
    reference = weakref.ref(remote)
    del remote
    gc.collect()
    assert reference() is None
    assert local.pair(local.start_pairing(), "Local phone")


def test_delayed_owner_click_cannot_publish_a_replacement_window(tmp_path):
    from server.remote_pairing import RemotePairingError
    published = []
    local, remote, _ = manager(tmp_path, publish=published.append)
    local.start_pairing()
    original_window = local.pairing_window()
    local.start_pairing()
    with pytest.raises(RemotePairingError) as error:
        remote.open(window_generation=original_window.generation)
    assert error.value.code == ErrorCode.EXPIRED_PAIRING
    assert published == []
    assert local.pair(local.active_code()[0], "Local phone")


def test_rejected_closure_marks_remote_offline_and_notifies_host(tmp_path):
    from server.remote_pairing import RemotePairingError
    errors = []
    def reject(handle):
        raise OSError("close queue rejected")
    local, remote, _ = manager(tmp_path, close=reject, on_error=errors.append)
    local.start_pairing()
    invitation = remote.open()
    replacement = local.start_pairing()
    assert remote.active_invitation() is None
    assert len(errors) == 1
    assert errors[0].code == ErrorCode.UNAVAILABLE
    assert remote.last_error is errors[0]
    assert_error(remote, invitation.handle, replacement, ErrorCode.OFFLINE)
    with pytest.raises(RemotePairingError) as error:
        remote.open()
    assert error.value.code == ErrorCode.OFFLINE
    assert local.pair(replacement, "Local phone")
    remote.set_online(True)
    assert remote.last_error is None
    local.start_pairing()
    assert remote.open().handle != invitation.handle


@pytest.mark.parametrize("reject_notification", [False, True])
def test_rejected_shutdown_closure_detaches_observer_and_retains_error(tmp_path, reject_notification):
    import gc
    import weakref
    errors = []
    def reject(handle):
        raise OSError("close queue rejected")
    def notify(error):
        errors.append(error)
        if reject_notification:
            raise OSError("notification queue rejected")
    local, remote, _ = manager(tmp_path, close=reject, on_error=notify)
    code = local.start_pairing()
    invitation = remote.open()
    remote.shutdown()
    assert remote.active_invitation() is None
    assert remote.last_error.code == ErrorCode.UNAVAILABLE
    assert errors == [remote.last_error]
    assert_error(remote, invitation.handle, code, ErrorCode.OFFLINE)
    reference = weakref.ref(remote)
    del remote
    gc.collect()
    assert reference() is None
    assert local.pair(code, "Local phone")
