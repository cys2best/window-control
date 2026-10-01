import json

import pytest

from server.pairing import (
    CODE_TTL_SECONDS,
    MAX_FAILED_ATTEMPTS,
    PairingStore,
    bearer_token,
)


class FakeClock:
    def __init__(self, now: float = 1_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_no_code_is_active_until_pairing_is_started():
    store = PairingStore()
    assert store.active_code() is None
    assert store.pair("000000", "Phone") is None


def test_start_pairing_returns_a_six_digit_code_with_the_full_ttl():
    store = PairingStore(clock=FakeClock())
    code = store.start_pairing()
    assert len(code) == 6 and code.isdigit()
    assert store.active_code() == (code, CODE_TTL_SECONDS)


def test_correct_code_returns_a_token_and_is_single_use():
    store = PairingStore()
    code = store.start_pairing()
    token = store.pair(code, "Phone")
    assert token and store.is_valid_token(token)
    assert store.active_code() is None
    assert store.pair(code, "Second phone") is None
    assert [d.name for d in store.list_devices()] == ["Phone"]


def test_code_is_accepted_with_spaces():
    store = PairingStore()
    code = store.start_pairing()
    assert store.pair(f"  {code[:3]} {code[3:]} ", "Phone") is not None


def test_code_expires_after_the_ttl():
    clock = FakeClock()
    store = PairingStore(clock=clock)
    code = store.start_pairing()
    clock.now += CODE_TTL_SECONDS - 1
    assert store.active_code() == (code, 1)
    clock.now += 1
    assert store.active_code() is None
    assert store.pair(code, "Phone") is None


def test_code_is_cleared_after_too_many_wrong_attempts():
    store = PairingStore()
    code = store.start_pairing()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(MAX_FAILED_ATTEMPTS):
        assert store.pair(wrong, "Attacker") is None
    assert store.active_code() is None
    assert store.pair(code, "Phone") is None


def test_one_fewer_wrong_attempt_still_allows_the_right_code():
    store = PairingStore()
    code = store.start_pairing()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        assert store.pair(wrong, "Attacker") is None
    assert store.pair(code, "Phone") is not None


def test_starting_again_replaces_the_code_and_resets_attempts():
    store = PairingStore()
    first = store.start_pairing()
    wrong = "000000" if first != "000000" else "111111"
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        store.pair(wrong, "Attacker")
    second = store.start_pairing()
    wrong = "000000" if second != "000000" else "111111"
    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        store.pair(wrong, "Attacker")
    assert store.pair(second, "Phone") is not None


@pytest.mark.parametrize("code", [None, 123456, "", "abcdef"])
def test_malformed_codes_are_rejected_without_raising(code):
    store = PairingStore()
    store.start_pairing()
    assert store.pair(code, "Phone") is None


@pytest.mark.parametrize("token", [None, "", "unknown", "x" * 300, 123])
def test_unknown_or_malformed_tokens_are_invalid(token):
    store = PairingStore()
    store.pair(store.start_pairing(), "Phone")
    assert store.is_valid_token(token) is False


def test_only_the_token_hash_is_written_to_disk(tmp_path):
    path = tmp_path / "paired_devices.json"
    store = PairingStore(str(path))
    token = store.pair(store.start_pairing(), "Phone")
    text = path.read_text()
    assert token not in text
    saved = json.loads(text)["devices"][0]
    assert saved["name"] == "Phone"
    assert len(saved["token_sha256"]) == 64


def test_devices_survive_a_restart(tmp_path):
    path = str(tmp_path / "paired_devices.json")
    first = PairingStore(path)
    token = first.pair(first.start_pairing(), "Phone")
    second = PairingStore(path)
    assert second.is_valid_token(token)
    assert [d.name for d in second.list_devices()] == ["Phone"]
    assert second.active_code() is None


def test_store_directory_is_created_on_first_save(tmp_path):
    path = tmp_path / "nested" / "dir" / "paired_devices.json"
    store = PairingStore(str(path))
    store.pair(store.start_pairing(), "Phone")
    assert path.exists()


@pytest.mark.parametrize("content", ["", "{not json", "[]", '{"devices": "nope"}',
                                     '{"devices": [{"id": 1}]}'])
def test_corrupt_store_file_starts_empty_and_is_rewritten(tmp_path, content):
    path = tmp_path / "paired_devices.json"
    path.write_text(content)
    store = PairingStore(str(path))
    assert store.list_devices() == []
    token = store.pair(store.start_pairing(), "Phone")
    assert PairingStore(str(path)).is_valid_token(token)


def test_unwritable_store_still_pairs_in_memory(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file where a directory is needed")
    store = PairingStore(str(blocker / "paired_devices.json"))
    token = store.pair(store.start_pairing(), "Phone")
    assert store.is_valid_token(token)


def test_remove_device_revokes_only_that_device():
    store = PairingStore()
    phone = store.pair(store.start_pairing(), "Phone")
    tablet = store.pair(store.start_pairing(), "Tablet")
    phone_id = store.list_devices()[0].id
    assert store.remove_device(phone_id) is True
    assert store.remove_device(phone_id) is False
    assert store.is_valid_token(phone) is False
    assert store.is_valid_token(tablet) is True


def test_remove_all_revokes_every_device(tmp_path):
    path = str(tmp_path / "paired_devices.json")
    store = PairingStore(path)
    token = store.pair(store.start_pairing(), "Phone")
    store.remove_all()
    assert store.list_devices() == []
    assert store.is_valid_token(token) is False
    assert PairingStore(path).list_devices() == []


@pytest.mark.parametrize("given,expected", [
    ("  Kitchen iPad  ", "Kitchen iPad"),
    ("", "Device"),
    ("   ", "Device"),
    (None, "Device"),
    ("n" * 200, "n" * 64),
])
def test_device_names_are_trimmed_capped_and_defaulted(given, expected):
    store = PairingStore()
    store.pair(store.start_pairing(), given)
    assert store.list_devices()[0].name == expected


@pytest.mark.parametrize("header,expected", [
    ("Bearer abc", "abc"),
    (None, None),
    ("", None),
    ("abc", None),
    ("bearer abc", None),
    ("Bearer ", None),
    ("Bearer  abc", None),
    ("Bearer a b", None),
])
def test_bearer_token_accepts_exactly_one_credential(header, expected):
    assert bearer_token(header) == expected


def _unwritable_path(tmp_path):
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory")
    return str(blocker / "paired_devices.json")


def test_last_save_ok_tracks_the_most_recent_save(tmp_path):
    store = PairingStore(_unwritable_path(tmp_path))
    assert store.last_save_ok is True
    token = store.pair(store.start_pairing(), "Phone")
    assert store.last_save_ok is False
    # revocation still takes effect in memory
    device_id = store.list_devices()[0].id
    assert store.remove_device(device_id) is True
    assert store.last_save_ok is False
    assert not store.is_valid_token(token)

    store.pair(store.start_pairing(), "Tablet")
    store.remove_all()
    assert store.list_devices() == []
    assert store.last_save_ok is False

    good = PairingStore(str(tmp_path / "ok.json"))
    good.pair(good.start_pairing(), "Phone")
    good.remove_all()
    assert good.last_save_ok is True


def test_failed_save_log_mentions_revocations(tmp_path, caplog):
    store = PairingStore(_unwritable_path(tmp_path))
    store.pair(store.start_pairing(), "Phone")
    assert "revocations" in caplog.text
