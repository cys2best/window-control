"""Tests for scripts/verify_lib.py's shared plumbing."""

from __future__ import annotations

import json
import os
import time

import pytest

from scripts.verify_lib import (
    CutoverFilePromptChannel,
    OwnedProcess,
    VerifyLibError,
    _pid_started_at,
    _read_json,
    _valid_prompt,
    _write_json_atomic,
    submit_file_confirmation,
)


def test_owned_process_is_frozen_dataclass():
    process = OwnedProcess(kind="app", pid=123, started_at=1.5)
    assert process.kind == "app"
    with pytest.raises(AttributeError):
        process.pid = 456  # type: ignore[misc]


def test_write_json_atomic_then_read_round_trips(tmp_path):
    target = tmp_path / "nested" / "result.json"
    _write_json_atomic(target, {"status": "PASS"})
    assert _read_json(target) == {"status": "PASS"}


def test_read_json_returns_none_for_missing_file(tmp_path):
    assert _read_json(tmp_path / "missing.json") is None


def test_read_json_returns_none_for_non_dict_json(tmp_path):
    path = tmp_path / "list.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    assert _read_json(path) is None


def test_pid_started_at_matches_this_own_process():
    started = _pid_started_at(os.getpid())
    assert started is not None
    assert started > 0


def test_pid_started_at_returns_none_for_a_pid_that_does_not_exist():
    # PID 999999 is not a real process on any supported platform in CI.
    assert _pid_started_at(999999) is None


def test_valid_prompt_accepts_a_well_formed_prompt():
    prompt = {
        "version": 1,
        "verifier_pid": 1,
        "verifier_started_at": 1.0,
        "nonce": "abc",
        "checkpoint": "gate",
        "message": "do the thing",
        "expected_results": ["PASS", "FAIL"],
    }
    assert _valid_prompt(prompt) is True


def test_valid_prompt_rejects_missing_fields():
    assert _valid_prompt({"version": 1}) is False


def test_valid_prompt_rejects_none():
    assert _valid_prompt(None) is False


def test_file_prompt_channel_round_trip_pass(tmp_path):
    evidence_dir = tmp_path / "engine" / "test" / "run-1"
    evidence_dir.mkdir(parents=True)
    channel = CutoverFilePromptChannel(evidence_dir, poll_seconds=0.01)

    def answer_from_another_process():
        # Wait for the prompt file the channel writes, then submit PASS
        # via the real submit_file_confirmation entry point, exactly as
        # a real second-terminal `-Confirm PASS` invocation would.
        deadline = time.monotonic() + 5
        prompt_path = evidence_dir / CutoverFilePromptChannel.PROMPT_FILENAME
        while not prompt_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert prompt_path.exists(), "prompt file was never written"
        submit_file_confirmation(
            tmp_path,
            "PASS",
            evidence_glob="run-*",
        )

    import threading

    responder = threading.Thread(target=answer_from_another_process)
    responder.start()
    result = channel.prompt("please confirm", checkpoint="my_gate")
    responder.join(timeout=5)
    assert result == "PASS"
    assert not (evidence_dir / CutoverFilePromptChannel.PROMPT_FILENAME).exists()


def test_submit_file_confirmation_rejects_invalid_result(tmp_path):
    with pytest.raises(VerifyLibError, match="PASS or FAIL"):
        submit_file_confirmation(tmp_path, "MAYBE", evidence_glob="anything-*")


def test_submit_file_confirmation_raises_when_no_prompt_is_active(tmp_path):
    (tmp_path / "engine" / "test" / "frontend-cutover-x").mkdir(parents=True)
    with pytest.raises(VerifyLibError, match="no live active"):
        submit_file_confirmation(tmp_path, "PASS", evidence_glob="frontend-cutover-*")


def test_submit_file_confirmation_rejects_a_stale_prompt_from_a_dead_pid(tmp_path):
    evidence_dir = tmp_path / "engine" / "test" / "frontend-cutover-x"
    evidence_dir.mkdir(parents=True)
    _write_json_atomic(
        evidence_dir / CutoverFilePromptChannel.PROMPT_FILENAME,
        {
            "version": 1,
            "verifier_pid": 999999,  # not a live process
            "verifier_started_at": 1.0,
            "nonce": "deadnonce",
            "checkpoint": "gate",
            "message": "msg",
            "expected_results": ["PASS", "FAIL"],
        },
    )
    with pytest.raises(VerifyLibError, match="no live active"):
        submit_file_confirmation(tmp_path, "PASS", evidence_glob="frontend-cutover-*")


def test_submit_file_confirmation_rejects_answering_the_same_prompt_twice(tmp_path):
    evidence_dir = tmp_path / "engine" / "test" / "frontend-cutover-x"
    channel = CutoverFilePromptChannel(evidence_dir, poll_seconds=0.01)
    import threading

    results: list[str] = []

    def responder_thread(sink: list[str]):
        deadline = time.monotonic() + 5
        prompt_path = evidence_dir / CutoverFilePromptChannel.PROMPT_FILENAME
        while not prompt_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        try:
            submit_file_confirmation(tmp_path, "PASS", evidence_glob="frontend-cutover-*")
            submit_file_confirmation(tmp_path, "PASS", evidence_glob="frontend-cutover-*")
        except VerifyLibError as error:
            sink.append(str(error))

    responder = threading.Thread(target=responder_thread, args=(results,))
    responder.start()
    channel.prompt("please confirm", checkpoint="my_gate")
    responder.join(timeout=5)
    assert any("already submitted" in message or "no live active" in message for message in results)
