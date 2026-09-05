"""Shared plumbing for the Windows cutover verifier scripts.

Pure process/file/JSON infrastructure with no domain knowledge of engine
cutover or frontend cutover specifics — both scripts/verify_engine_cutover.py
and scripts/verify_frontend_cutover.py import from here so there is one
file-prompt/evidence implementation, not two.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class VerifyLibError(RuntimeError):
    pass


@dataclass(frozen=True)
class OwnedProcess:
    kind: str
    pid: int
    started_at: float


def _safe_detail(value: Any) -> str:
    detail = " ".join(str(value).split())
    return detail[:477] + "..." if len(detail) > 480 else detail


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    )
    try:
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _pid_started_at(pid: int) -> float | None:
    import psutil

    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return None


class CutoverFilePromptChannel:
    """Phase-2-compatible nonce/PID/start-time-scoped file prompt channel."""

    PROMPT_FILENAME = "active-prompt.json"
    RESPONSE_PREFIX = "prompt-response-"

    def __init__(
        self,
        evidence_dir: Path,
        *,
        verifier_pid: int | None = None,
        poll_seconds: float = 0.25,
        record_event: Callable[[str], None] | None = None,
    ):
        self.evidence_dir = evidence_dir
        self.verifier_pid = verifier_pid if verifier_pid is not None else os.getpid()
        self.verifier_started_at = _pid_started_at(self.verifier_pid)
        if self.verifier_started_at is None:
            raise VerifyLibError("could not determine live verifier process start time")
        self.poll_seconds = poll_seconds
        self.record_event = record_event
        self.prompt_path = evidence_dir / self.PROMPT_FILENAME

    @classmethod
    def response_path(cls, evidence_dir: Path, nonce: str) -> Path:
        digest = hashlib.sha256(nonce.encode("utf-8")).hexdigest()
        return evidence_dir / f"{cls.RESPONSE_PREFIX}{digest}.json"

    def _event(self, message: str) -> None:
        if self.record_event is not None:
            self.record_event(message)

    def _remove(self, path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            self._event(f"prompt cleanup unavailable for {path.name}: {_safe_detail(error)}")

    def cleanup(self) -> None:
        self._remove(self.prompt_path)
        for path in self.evidence_dir.glob(f"{self.RESPONSE_PREFIX}*.json"):
            self._remove(path)

    def prompt(self, message: str, checkpoint: str) -> str:
        self.cleanup()
        nonce = secrets.token_hex(16)
        prompt = {
            "version": 1,
            "verifier_pid": self.verifier_pid,
            "verifier_started_at": self.verifier_started_at,
            "nonce": nonce,
            "checkpoint": checkpoint,
            "message": message,
            "expected_results": ["PASS", "FAIL"],
        }
        _write_json_atomic(self.prompt_path, prompt)
        response_path = self.response_path(self.evidence_dir, nonce)
        notice = (
            f"CHECKPOINT: {checkpoint}\n{message}\n\n"
            "Waiting for file confirmation. Submit it with this repo's "
            "matching verifier's -Confirm PASS (or -Confirm FAIL)."
        )
        print(notice, flush=True)
        self._event(notice)
        try:
            while True:
                if response_path.exists():
                    response = _read_json(response_path)
                    self._remove(response_path)
                    if (
                        response is not None
                        and response.get("version") == 1
                        and response.get("verifier_pid") == self.verifier_pid
                        and response.get("verifier_started_at") == self.verifier_started_at
                        and response.get("nonce") == nonce
                        and response.get("result") in {"PASS", "FAIL"}
                    ):
                        return str(response["result"])
                    self._event(f"ignored mismatched confirmation for {checkpoint}")
                time.sleep(self.poll_seconds)
        finally:
            self.cleanup()


def _valid_prompt(value: dict[str, Any] | None) -> bool:
    return bool(
        value
        and value.get("version") == 1
        and type(value.get("verifier_pid")) is int
        and value["verifier_pid"] > 0
        and type(value.get("verifier_started_at")) in {int, float}
        and value["verifier_started_at"] > 0
        and isinstance(value.get("nonce"), str)
        and value["nonce"]
        and isinstance(value.get("checkpoint"), str)
        and isinstance(value.get("message"), str)
        and value.get("expected_results") == ["PASS", "FAIL"]
    )


def submit_file_confirmation(repo_root: Path, result: str, *, evidence_glob: str) -> Path:
    result = result.upper()
    if result not in {"PASS", "FAIL"}:
        raise VerifyLibError("file confirmation must be PASS or FAIL")
    active: list[tuple[Path, dict[str, Any]]] = []
    for prompt_path in (repo_root / "engine" / "test").glob(
        f"{evidence_glob}/{CutoverFilePromptChannel.PROMPT_FILENAME}"
    ):
        prompt = _read_json(prompt_path)
        if _valid_prompt(prompt) and _pid_started_at(prompt["verifier_pid"]) == prompt["verifier_started_at"]:
            active.append((prompt_path, prompt))
    if not active:
        raise VerifyLibError(f"no live active {evidence_glob!r}-matching file prompt found")
    if len(active) != 1:
        raise VerifyLibError(f"multiple live {evidence_glob!r}-matching prompts found ({len(active)})")
    prompt_path, prompt = active[0]
    current = _read_json(prompt_path)
    if (
        not _valid_prompt(current)
        or current.get("nonce") != prompt["nonce"]
        or current.get("verifier_pid") != prompt["verifier_pid"]
        or current.get("verifier_started_at") != prompt["verifier_started_at"]
        or _pid_started_at(prompt["verifier_pid"]) != prompt["verifier_started_at"]
    ):
        raise VerifyLibError("active file prompt changed; retry confirmation")
    response_path = CutoverFilePromptChannel.response_path(prompt_path.parent, prompt["nonce"])
    temporary = response_path.with_name(f".{response_path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temporary.write_text(
            json.dumps(
                {
                    "version": 1,
                    "verifier_pid": prompt["verifier_pid"],
                    "verifier_started_at": prompt["verifier_started_at"],
                    "nonce": prompt["nonce"],
                    "result": result,
                }
            ),
            encoding="utf-8",
        )
        try:
            os.link(temporary, response_path)
        except FileExistsError as error:
            raise VerifyLibError("confirmation already submitted for this prompt") from error
    finally:
        temporary.unlink(missing_ok=True)
    return response_path
