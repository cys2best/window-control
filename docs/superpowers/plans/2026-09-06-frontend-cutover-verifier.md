# Frontend/Desktop Cutover Verifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Automate the build/HTTP/process-level checks in
`docs/WINDOWS_MANUAL_VALIDATION.md` (the manual runbook covering
`2026-09-05-react-unified-frontend`'s never-verified-on-Windows surface)
into a new `scripts/verify_frontend_cutover.py` + `engine/verify-frontend-cutover.ps1`,
following the existing `verify_engine_cutover.py`/`.ps1` pattern exactly,
while leaving genuinely-manual checks (WebView2 visual confirmation, the
leaked-key cross-machine test, the Supabase two-account browser flow) as
file-prompt gates the operator answers.

**Architecture:** A dependency-injected state machine
(`FrontendCutoverConfig` → `run(config, deps) → FrontendCutoverResult`)
identical in shape to the existing tool's `CutoverConfig`/`run_cutover_verification`/`CutoverResult`,
tested via a `FakeDeps` double with zero real subprocess/network calls.
Five pieces of pure plumbing (`OwnedProcess`, the JSON atomic
read/write helpers, `_pid_started_at`, and the file-prompt channel +
`submit_file_confirmation`) move out of `verify_engine_cutover.py` into a
new shared `scripts/verify_lib.py` so both tools use one file-prompt
implementation, not two.

**Tech Stack:** Python 3.11+ (`uv run`), `httpx` for HTTP, `psutil` for
process identity, `pytest` for tests, PowerShell 5+ for the CLI wrapper.

**Spec:** `docs/superpowers/specs/2026-09-06-frontend-cutover-verifier-design.md`

## Global Constraints

- `scripts/verify_engine_cutover.py`'s own domain logic (ADB/browser/WHEP
  orchestration, `CutoverConfig`/`CutoverResult`/`RealCutoverDeps`) does
  not change. Only its five named plumbing pieces move to `verify_lib.py`,
  plus its one `submit_file_confirmation(...)` call site gains an explicit
  `evidence_glob="engine-cutover-*"` keyword argument (see Task 1 — this
  is the one real line-level change beyond the import swap, needed because
  the function's glob pattern must become a parameter for the new tool to
  reuse it with its own `"frontend-cutover-*"` prefix).
- `tests/test_engine_cutover_verifier.py` (1611 lines, existing) must stay
  green, unmodified, after Task 1's extraction — that is the acceptance
  gate proving the extraction changed no observable behavior.
- New gate-name vocabulary for the frontend tool: `PASS | FAIL | SKIPPED`
  per gate, overall `status: PASS | FAIL | INCOMPLETE` — this is
  deliberately not identical to the existing tool's
  `PASS/FAIL/SKIP`/`checkpoints` vocabulary (reviewed and approved in the
  spec); do not "fix" this into matching the older file.
- Gate names (exact, used in code, tests, and evidence JSON keys):
  `dev_app_health`, `web_routes`, `rsc_payloads`, `instances_negotiation`,
  `auth_gate`, `offline_suites`, `installed_app_launch`,
  `frozen_selfrelaunch`, `desktop_shell_visual`,
  `supabase_two_account_flow`, `leaked_key_forgery_check`.
- Real, confirmed-by-reading-the-code values to use verbatim (do not
  re-derive or guess — these were checked against the actual repo state
  while writing this plan):
  - `/auth/config` (src/server/app.py:421-427) returns
    `{"auth_enabled": bool, "supabase_url": str, "supabase_anon_key": str}`.
    `auth_enabled` is the exact field name gate `auth_gate` keys its
    self-skip off.
  - App port default: `8080` (`src/config.py:4`, `PORT = 8080`).
  - Installed app path: `C:\Program Files\WindowControl\WindowControl.exe`
    (`build/installer.iss`: `DefaultDirName={autopf}\WindowControl`,
    `MyAppExeName = "WindowControl.exe"`, and `ArchitecturesInstallIn64BitMode=x64compatible`
    elsewhere in the same file resolves `{autopf}` to `Program Files`, not
    `Program Files (x86)`).
  - Engine binary inside an install:
    `C:\Program Files\WindowControl\_internal\assets\engine\engine.exe`
    (PyInstaller onedir layout, confirmed via `build/window_control.spec`'s
    `datas` staging `apps/web/out` as `'web'` alongside the equivalent
    `assets/engine` staging).
  - Firewall rule name: `"WindowControl-Engine"` (`build/installer.iss`,
    `AddEngineFirewallRule()`).
  - `apps/web` page shells with real `.txt` RSC-payload siblings:
    `index`, `login`, `setup`, `instances`, `stream` (confirmed against
    Task 10's own report and `src/server/app.py`'s five explicit
    `@app.get` page routes at lines 364-379 plus the generic
    `@app.get("/{page}.txt")` handler at line ~395).

---

### Task 1: Extract shared plumbing into `scripts/verify_lib.py`

**Files:**
- Create: `scripts/verify_lib.py`
- Create: `tests/test_verify_lib.py`
- Modify: `scripts/verify_engine_cutover.py` (remove the extracted
  definitions, add `from scripts.verify_lib import (...)` at the top,
  update the one `submit_file_confirmation` call site)

**Interfaces:**
- Produces (used by Task 2 onward, and by the modified
  `verify_engine_cutover.py`):
  - `class CutoverError(RuntimeError)` — stays in `verify_engine_cutover.py`
    unchanged; `verify_lib.py` raises its own `VerifyLibError(RuntimeError)`
    instead (the plumbing shouldn't import the domain module to raise the
    domain's error type — that would be a backwards dependency).
  - `@dataclass(frozen=True) class OwnedProcess: kind: str; pid: int; started_at: float`
  - `def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None`
  - `def _read_json(path: Path) -> dict[str, Any] | None`
  - `def _pid_started_at(pid: int) -> float | None`
  - `class CutoverFilePromptChannel` — identical to today's, with
    `prompt(self, message: str, checkpoint: str) -> str` still printing
    the fixed notice
    `r".\engine\verify-engine-cutover.ps1 -Confirm PASS"` — **this fixed
    string in the notice text does NOT change in this task**; Task 2
    handles making the printed hint tool-aware (see Task 2's Interfaces).
  - `def _valid_prompt(value: dict[str, Any] | None) -> bool`
  - `def submit_file_confirmation(repo_root: Path, result: str, *, evidence_glob: str) -> Path`
    — same body as today except the hardcoded `"engine-cutover-*"` glob
    segment becomes the `evidence_glob` parameter, and the "no live
    active..." error message interpolates it:
    `f"no live active {evidence_glob!r}-matching file prompt found"`.

- [ ] **Step 1: Create `scripts/verify_lib.py` with the extracted code**

```python
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
```

Note: the prompt notice text changed from hardcoding
`.\engine\verify-engine-cutover.ps1 -Confirm PASS` to the tool-agnostic
`"this repo's matching verifier's -Confirm PASS"`. This is a deliberate,
minimal behavior change to the moved code (the old hardcoded hint would be
actively wrong advice when printed from the new frontend tool). It is
covered by Step 5's assertion below and does not affect any of
`tests/test_engine_cutover_verifier.py`'s existing assertions (checked: that
suite asserts on `checkpoint`/`nonce`/`result` fields in the prompt/response
JSON, never on the literal printed notice string).

- [ ] **Step 2: Write `tests/test_verify_lib.py`**

```python
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
    channel = CutoverFilePromptChannel(tmp_path, poll_seconds=0.01)

    def answer_from_another_process():
        # Wait for the prompt file the channel writes, then submit PASS
        # via the real submit_file_confirmation entry point, exactly as
        # a real second-terminal `-Confirm PASS` invocation would.
        deadline = time.monotonic() + 5
        prompt_path = tmp_path / CutoverFilePromptChannel.PROMPT_FILENAME
        while not prompt_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert prompt_path.exists(), "prompt file was never written"
        submit_file_confirmation(
            tmp_path.parent.parent,
            "PASS",
            evidence_glob=tmp_path.parent.name + "/" + tmp_path.name,
        )

    import threading

    responder = threading.Thread(target=answer_from_another_process)
    responder.start()
    result = channel.prompt("please confirm", checkpoint="my_gate")
    responder.join(timeout=5)
    assert result == "PASS"
    assert not (tmp_path / CutoverFilePromptChannel.PROMPT_FILENAME).exists()


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
```

- [ ] **Step 3: Run the new tests to confirm they pass**

Run: `uv run pytest tests/test_verify_lib.py -v`
Expected: all tests PASS (this is new code with no prior RED step needed —
it's a direct extraction of already-working logic, verified by these
tests rather than by a TDD RED/GREEN cycle on brand-new behavior).

- [ ] **Step 4: Remove the extracted definitions from `scripts/verify_engine_cutover.py` and import from `verify_lib`**

At the top of `scripts/verify_engine_cutover.py`, after the existing
`import httpx` line, add:

```python
from scripts.verify_lib import (
    CutoverFilePromptChannel,
    OwnedProcess,
    _pid_started_at,
    _read_json,
    _valid_prompt,
    _write_json_atomic,
    submit_file_confirmation as _lib_submit_file_confirmation,
)
```

Delete these definitions from the file body (they now come from the
import above): `OwnedProcess` (lines 75-79), `_write_json_atomic`
(142-151), `_read_json` (154-159), `_pid_started_at` (162-168),
`CutoverFilePromptChannel` (171-253), `_valid_prompt` (256-269), and the
module-level `submit_file_confirmation` function (272-318) — replace that
last one with a thin wrapper that supplies this tool's own fixed glob so
every existing internal caller keeps working unchanged:

```python
def submit_file_confirmation(repo_root: Path, result: str) -> Path:
    return _lib_submit_file_confirmation(repo_root, result, evidence_glob="engine-cutover-*")
```

(Keep `CutoverError` defined locally in `verify_engine_cutover.py` as
before — it is not part of the extraction.)

- [ ] **Step 5: Run the existing test suite to confirm the extraction changed nothing observable**

Run: `uv run pytest tests/test_engine_cutover_verifier.py -v`
Expected: same result as before this task (all tests that passed before
still pass; zero edits were made to this test file). If anything fails,
the extraction introduced a behavior change — find and fix it before
proceeding; do not edit the test file to make it pass.

- [ ] **Step 6: Run the full suite to check for collateral effects**

Run: `uv run pytest tests/ -q --continue-on-collection-errors`
Expected: same shape as the documented baseline (456 passed / 2
pre-existing `test_windows_verifier.py` failures / 1 skipped / 2
pre-existing collection errors), plus the new `test_verify_lib.py` tests
now passing on top.

- [ ] **Step 7: Commit**

```bash
git add scripts/verify_lib.py scripts/verify_engine_cutover.py tests/test_verify_lib.py
git commit -m "refactor: extract shared cutover-verifier plumbing into verify_lib"
```

---

### Task 2: Scaffold `verify_frontend_cutover.py`'s config, result, and gate-selection engine

**Files:**
- Create: `scripts/verify_frontend_cutover.py`
- Create: `tests/test_frontend_cutover_verifier.py`

**Interfaces:**
- Consumes: `scripts.verify_lib.{OwnedProcess, CutoverFilePromptChannel, VerifyLibError, submit_file_confirmation, _write_json_atomic, _read_json, _pid_started_at}` (Task 1).
- Produces (used by Tasks 3-6):
  - `GATE_NAMES: tuple[str, ...]` — the 11 gate names in fixed run order,
    exactly: `("dev_app_health", "web_routes", "rsc_payloads", "instances_negotiation", "auth_gate", "offline_suites", "installed_app_launch", "frozen_selfrelaunch", "desktop_shell_visual", "supabase_two_account_flow", "leaked_key_forgery_check")`.
  - `GATE_DEPENDENCIES: dict[str, tuple[str, ...]]` — `{"web_routes": ("dev_app_health",), "rsc_payloads": ("dev_app_health",), "instances_negotiation": ("dev_app_health",), "auth_gate": ("dev_app_health",), "frozen_selfrelaunch": ("installed_app_launch",)}` (gates absent from this dict have no dependencies).
  - `class FrontendCutoverError(RuntimeError)`
  - `@dataclass class FrontendCutoverConfig` (fields: `repo_root: Path`, `evidence_dir: Path`, `web_build_dir: Path`, `installer_path: Path`, `port: int = 8080`, `file_prompts: bool = False`, `file_prompt_poll_seconds: float = 0.25`, `keep_on_failure: bool = False`, `skip_manual_gates: bool = False`, `skip_installer: bool = False`, `only: tuple[str, ...] | None = None`, `from_gate: str | None = None`)
  - `@dataclass class FrontendCutoverResult` with `status: str = "PASS"`, `gates: dict[str, dict[str, Any]] = field(default_factory=dict)`, method `mark(self, name: str, status: str, *, reason: str, details: dict[str, Any] | None = None) -> None` (statuses: `PASS`/`FAIL`/`SKIPPED`; `FAIL` sets `self.status = "FAIL"`; `SKIPPED` sets `self.status = "INCOMPLETE"` unless already `"FAIL"`), and `to_dict(self) -> dict[str, Any]` returning `{"status": ..., "gates": ...}` (plus `"selection": {"mode": ..., "requested": [...]}` only when `only`/`from_gate` was used — see Step 4).
  - `def resolve_gate_selection(config: FrontendCutoverConfig) -> dict[str, str]` — pure function, no I/O, returns `{gate_name: reason}` for every gate in `GATE_NAMES`, where `reason` is one of: `"requested"`, `f"auto-included as prerequisite of {dependent}"`, `"not selected this run (--only)"`, `"not selected this run (--from)"`. This is the gate-selection closure logic Step 3 tests in isolation before any real gate exists.

- [ ] **Step 1: Write the failing tests for `resolve_gate_selection`**

```python
"""Tests for scripts/verify_frontend_cutover.py."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.verify_frontend_cutover import (
    GATE_NAMES,
    FrontendCutoverConfig,
    FrontendCutoverResult,
    resolve_gate_selection,
)


def _config(**overrides) -> FrontendCutoverConfig:
    defaults = dict(
        repo_root=Path("/repo"),
        evidence_dir=Path("/repo/engine/test/frontend-cutover-x"),
        web_build_dir=Path("/repo/apps/web/out"),
        installer_path=Path("/repo/release/WindowControlInstaller.exe"),
    )
    defaults.update(overrides)
    return FrontendCutoverConfig(**defaults)


def test_full_run_selects_every_gate_as_requested():
    selection = resolve_gate_selection(_config())
    assert set(selection) == set(GATE_NAMES)
    assert all(reason == "requested" for reason in selection.values())


def test_only_a_dependency_free_gate_selects_just_that_gate():
    selection = resolve_gate_selection(_config(only=("offline_suites",)))
    assert selection["offline_suites"] == "requested"
    for name in GATE_NAMES:
        if name != "offline_suites":
            assert selection[name] == "not selected this run (--only)"


def test_only_a_dependent_gate_auto_includes_its_prerequisite():
    selection = resolve_gate_selection(_config(only=("auth_gate",)))
    assert selection["auth_gate"] == "requested"
    assert selection["dev_app_health"] == "auto-included as prerequisite of auth_gate"
    assert selection["web_routes"] == "not selected this run (--only)"


def test_only_frozen_selfrelaunch_auto_includes_installed_app_launch():
    selection = resolve_gate_selection(_config(only=("frozen_selfrelaunch",)))
    assert selection["frozen_selfrelaunch"] == "requested"
    assert selection["installed_app_launch"] == "auto-included as prerequisite of frozen_selfrelaunch"


def test_only_accepts_a_comma_style_tuple_of_multiple_gates():
    selection = resolve_gate_selection(_config(only=("web_routes", "rsc_payloads")))
    assert selection["web_routes"] == "requested"
    assert selection["rsc_payloads"] == "requested"
    assert selection["dev_app_health"] == "auto-included as prerequisite of web_routes"
    assert selection["instances_negotiation"] == "not selected this run (--only)"


def test_from_a_gate_selects_it_and_everything_after_in_fixed_order():
    selection = resolve_gate_selection(_config(from_gate="offline_suites"))
    index = GATE_NAMES.index("offline_suites")
    for i, name in enumerate(GATE_NAMES):
        expected = "requested" if i >= index else "not selected this run (--from)"
        assert selection[name] == expected


def test_from_the_first_gate_selects_everything():
    selection = resolve_gate_selection(_config(from_gate="dev_app_health"))
    assert all(reason == "requested" for reason in selection.values())


def test_only_and_from_together_is_rejected():
    with pytest.raises(ValueError, match="mutually exclusive"):
        resolve_gate_selection(_config(only=("web_routes",), from_gate="offline_suites"))


def test_unknown_gate_name_in_only_is_rejected():
    with pytest.raises(ValueError, match="unknown gate"):
        resolve_gate_selection(_config(only=("not_a_real_gate",)))


def test_unknown_gate_name_in_from_is_rejected():
    with pytest.raises(ValueError, match="unknown gate"):
        resolve_gate_selection(_config(from_gate="not_a_real_gate"))


def test_result_mark_pass_keeps_status_pass():
    result = FrontendCutoverResult()
    result.mark("dev_app_health", "PASS", reason="requested")
    assert result.status == "PASS"
    assert result.gates["dev_app_health"] == {"status": "PASS", "reason": "requested", "details": {}}


def test_result_mark_fail_sets_overall_status_fail():
    result = FrontendCutoverResult()
    result.mark("web_routes", "FAIL", reason="requested", details={"error": "500"})
    assert result.status == "FAIL"


def test_result_mark_skipped_caps_status_at_incomplete_not_pass():
    result = FrontendCutoverResult()
    result.mark("leaked_key_forgery_check", "SKIPPED", reason="no second machine available")
    assert result.status == "INCOMPLETE"


def test_result_mark_skipped_does_not_downgrade_an_existing_fail():
    result = FrontendCutoverResult()
    result.mark("web_routes", "FAIL", reason="requested")
    result.mark("auth_gate", "SKIPPED", reason="Supabase auth not configured this run")
    assert result.status == "FAIL"


def test_to_dict_omits_selection_key_on_a_full_run():
    result = FrontendCutoverResult()
    result.mark("dev_app_health", "PASS", reason="requested")
    payload = result.to_dict()
    assert "selection" not in payload
    assert payload == {"status": "PASS", "gates": {"dev_app_health": {"status": "PASS", "reason": "requested", "details": {}}}}
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_frontend_cutover_verifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.verify_frontend_cutover'` (or similar import error).

- [ ] **Step 3: Create `scripts/verify_frontend_cutover.py` with the config/result/selection scaffold**

```python
"""Dependency-injected verifier for the frontend/desktop cutover surface
that scripts/verify_engine_cutover.py doesn't cover: apps/web's static
export routing, the apps/desktop pywebview shell, and the packaged
installer's frontend-specific pieces.

See docs/superpowers/specs/2026-09-06-frontend-cutover-verifier-design.md
for the full design this implements.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


GATE_NAMES: tuple[str, ...] = (
    "dev_app_health",
    "web_routes",
    "rsc_payloads",
    "instances_negotiation",
    "auth_gate",
    "offline_suites",
    "installed_app_launch",
    "frozen_selfrelaunch",
    "desktop_shell_visual",
    "supabase_two_account_flow",
    "leaked_key_forgery_check",
)

GATE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    "web_routes": ("dev_app_health",),
    "rsc_payloads": ("dev_app_health",),
    "instances_negotiation": ("dev_app_health",),
    "auth_gate": ("dev_app_health",),
    "frozen_selfrelaunch": ("installed_app_launch",),
}


class FrontendCutoverError(RuntimeError):
    pass


@dataclass
class FrontendCutoverConfig:
    repo_root: Path
    evidence_dir: Path
    web_build_dir: Path
    installer_path: Path
    port: int = 8080
    file_prompts: bool = False
    file_prompt_poll_seconds: float = 0.25
    keep_on_failure: bool = False
    skip_manual_gates: bool = False
    skip_installer: bool = False
    only: tuple[str, ...] | None = None
    from_gate: str | None = None


@dataclass
class FrontendCutoverResult:
    status: str = "PASS"
    gates: dict[str, dict[str, Any]] = field(default_factory=dict)

    def mark(
        self,
        name: str,
        status: str,
        *,
        reason: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.gates[name] = {"status": status, "reason": reason, "details": details or {}}
        if status == "FAIL":
            self.status = "FAIL"
        elif status == "SKIPPED" and self.status == "PASS":
            self.status = "INCOMPLETE"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"status": self.status, "gates": self.gates}
        return payload


def resolve_gate_selection(config: FrontendCutoverConfig) -> dict[str, str]:
    """Pure function: compute {gate_name: reason} for every gate in GATE_NAMES.

    No I/O, no side effects — this is the scheduling/dependency-closure
    logic in isolation, testable without any real gate implementation.
    """
    if config.only is not None and config.from_gate is not None:
        raise ValueError("--only and --from are mutually exclusive")

    for name in config.only or ():
        if name not in GATE_NAMES:
            raise ValueError(f"unknown gate {name!r}")
    if config.from_gate is not None and config.from_gate not in GATE_NAMES:
        raise ValueError(f"unknown gate {config.from_gate!r}")

    if config.only is None and config.from_gate is None:
        return {name: "requested" for name in GATE_NAMES}

    requested: set[str]
    not_selected_reason: str
    if config.only is not None:
        requested = set(config.only)
        not_selected_reason = "not selected this run (--only)"
    else:
        index = GATE_NAMES.index(config.from_gate)  # type: ignore[arg-type]
        requested = set(GATE_NAMES[index:])
        not_selected_reason = "not selected this run (--from)"

    # Compute the dependency closure: anything a requested gate needs,
    # transitively, that wasn't itself requested.
    needed: dict[str, str] = {}
    stack = list(requested)
    while stack:
        name = stack.pop()
        for dependency in GATE_DEPENDENCIES.get(name, ()):
            if dependency not in requested and dependency not in needed:
                needed[dependency] = f"auto-included as prerequisite of {name}"
                stack.append(dependency)

    selection: dict[str, str] = {}
    for name in GATE_NAMES:
        if name in requested:
            selection[name] = "requested"
        elif name in needed:
            selection[name] = needed[name]
        else:
            selection[name] = not_selected_reason
    return selection
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_frontend_cutover_verifier.py -v`
Expected: all tests PASS.

Note on `test_to_dict_omits_selection_key_on_a_full_run`: this test
passes as written because `to_dict()` never adds a `"selection"` key at
all in this scaffold — that key is added by the real `run()` function in
Task 6 (which has access to `resolve_gate_selection`'s output and the
config), not by `FrontendCutoverResult` itself. Confirm this test still
makes sense unchanged when Task 6 wires `run()` — it will, since `to_dict()`
itself never gains selection-awareness; `run()` merges the selection info
into the dict it prints, after calling `to_dict()`.

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_frontend_cutover.py tests/test_frontend_cutover_verifier.py
git commit -m "feat: scaffold frontend cutover verifier config, result, and gate selection"
```

---

### Task 3: HTTP gates — `dev_app_health`, `web_routes`, `rsc_payloads`, `instances_negotiation`, `auth_gate`

**Files:**
- Modify: `scripts/verify_frontend_cutover.py`
- Modify: `tests/test_frontend_cutover_verifier.py`

**Interfaces:**
- Consumes: `scripts.verify_lib.{OwnedProcess, _pid_started_at}` (Task 1); `GATE_NAMES`, `FrontendCutoverConfig`, `FrontendCutoverResult` (Task 2).
- Produces (used by Task 6's `run()`):
  - `class RealFrontendDeps` with methods:
    - `start_dev_app(self, environment: dict[str, str]) -> OwnedProcess`
    - `wait_for_dev_app(self, app: OwnedProcess, port: int) -> bool` (polls `GET http://127.0.0.1:{port}/auth/config` until 200 or the process dies or a 90s deadline, mirroring `verify_engine_cutover.py`'s `wait_for_app` polling shape but against `/auth/config` instead of `/instances` — `/auth/config` needs no auth header and exists specifically to answer "is the server up and is auth on")
    - `auth_config(self, port: int) -> dict[str, Any]` (one `GET /auth/config`, parsed JSON)
    - `get(self, port: int, path: str, *, headers: dict[str, str] | None = None) -> tuple[int, str, str]` returns `(status_code, content_type, body_text)` for a single request — the shared low-level HTTP helper every HTTP gate below uses
    - `terminate(self, process: OwnedProcess) -> None`
  - Five gate functions, each `def gate_x(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None` (uniform signature every gate in this tool follows — `deps: Any` because tests pass a `FakeDeps` duck-type, not a real `RealFrontendDeps`):
    `gate_dev_app_health`, `gate_web_routes`, `gate_rsc_payloads`,
    `gate_instances_negotiation`, `gate_auth_gate`.

- [ ] **Step 1: Write the failing tests**

```python
class FakeDeps:
    def __init__(self, *, auth_config: dict | None = None, responses: dict[tuple[int, str], tuple[int, str, str]] | None = None):
        self._auth_config = auth_config if auth_config is not None else {"auth_enabled": False, "supabase_url": "", "supabase_anon_key": ""}
        self._responses = responses or {}
        self.started_dev_app = False
        self.terminated: list[str] = []
        self.dev_app_wait_succeeds = True

    def start_dev_app(self, environment):
        self.started_dev_app = True
        return __import__("scripts.verify_lib", fromlist=["OwnedProcess"]).OwnedProcess("app", 4242, 1.0)

    def wait_for_dev_app(self, app, port):
        return self.dev_app_wait_succeeds

    def auth_config(self, port):
        return self._auth_config

    def get(self, port, path, *, headers=None):
        key = (port, path if headers is None else path + "|" + headers.get("Accept", ""))
        if key in self._responses:
            return self._responses[key]
        return self._responses.get((port, path), (404, "text/plain", "not found"))

    def terminate(self, process):
        self.terminated.append(process.kind)


def test_dev_app_health_passes_when_wait_succeeds():
    result = FrontendCutoverResult()
    deps = FakeDeps()
    gate_dev_app_health(_config(), deps, result, "requested")
    assert deps.started_dev_app is True
    assert result.gates["dev_app_health"]["status"] == "PASS"


def test_dev_app_health_fails_when_wait_times_out():
    result = FrontendCutoverResult()
    deps = FakeDeps()
    deps.dev_app_wait_succeeds = False
    gate_dev_app_health(_config(), deps, result, "requested")
    assert result.gates["dev_app_health"]["status"] == "FAIL"


def test_web_routes_passes_when_every_page_is_html_200():
    responses = {(8080, path): (200, "text/html; charset=utf-8", "<html></html>") for path in ("/", "/login", "/setup", "/stream")}
    result = FrontendCutoverResult()
    deps = FakeDeps(responses=responses)
    gate_web_routes(_config(), deps, result, "requested")
    assert result.gates["web_routes"]["status"] == "PASS"


def test_web_routes_fails_when_one_page_is_not_html():
    responses = {(8080, path): (200, "text/html; charset=utf-8", "<html></html>") for path in ("/", "/login", "/setup", "/stream")}
    responses[(8080, "/stream")] = (200, "application/json", "{}")
    result = FrontendCutoverResult()
    deps = FakeDeps(responses=responses)
    gate_web_routes(_config(), deps, result, "requested")
    assert result.gates["web_routes"]["status"] == "FAIL"
    assert "/stream" in result.gates["web_routes"]["details"]["failures"][0]


def test_rsc_payloads_passes_for_the_five_txt_files_plus_static_assets():
    responses = {}
    for page in ("index", "login", "setup", "instances", "stream"):
        responses[(8080, f"/{page}.txt")] = (200, "text/x-component; charset=utf-8", "chunk")
    responses[(8080, "/404.html")] = (200, "text/html; charset=utf-8", "<html></html>")
    responses[(8080, "/manifest.json")] = (200, "application/json", "{}")
    responses[(8080, "/icon-192.png")] = (200, "image/png", "\x89PNG")
    result = FrontendCutoverResult()
    deps = FakeDeps(responses=responses)
    gate_rsc_payloads(_config(), deps, result, "requested")
    assert result.gates["rsc_payloads"]["status"] == "PASS"


def test_rsc_payloads_fails_when_a_txt_payload_404s():
    responses = {}
    for page in ("index", "login", "setup", "instances", "stream"):
        responses[(8080, f"/{page}.txt")] = (200, "text/x-component; charset=utf-8", "chunk")
    responses[(8080, "/stream.txt")] = (404, "text/plain", "not found")
    responses[(8080, "/404.html")] = (200, "text/html; charset=utf-8", "<html></html>")
    responses[(8080, "/manifest.json")] = (200, "application/json", "{}")
    responses[(8080, "/icon-192.png")] = (200, "image/png", "\x89PNG")
    result = FrontendCutoverResult()
    deps = FakeDeps(responses=responses)
    gate_rsc_payloads(_config(), deps, result, "requested")
    assert result.gates["rsc_payloads"]["status"] == "FAIL"


def test_instances_negotiation_passes_when_json_and_html_shapes_are_correct():
    responses = {
        (8080, "/instances|"): (200, "application/json", "[]"),
        (8080, "/instances|application/json"): (200, "application/json", "[]"),
        (8080, "/instances|text/html"): (200, "text/html; charset=utf-8", "<html></html>"),
    }
    result = FrontendCutoverResult()
    deps = FakeDeps(responses=responses)
    gate_instances_negotiation(_config(), deps, result, "requested")
    assert result.gates["instances_negotiation"]["status"] == "PASS"


def test_instances_negotiation_fails_when_html_accept_still_returns_json():
    responses = {
        (8080, "/instances|"): (200, "application/json", "[]"),
        (8080, "/instances|application/json"): (200, "application/json", "[]"),
        (8080, "/instances|text/html"): (200, "application/json", "[]"),
    }
    result = FrontendCutoverResult()
    deps = FakeDeps(responses=responses)
    gate_instances_negotiation(_config(), deps, result, "requested")
    assert result.gates["instances_negotiation"]["status"] == "FAIL"


def test_auth_gate_self_skips_when_auth_is_disabled():
    result = FrontendCutoverResult()
    deps = FakeDeps(auth_config={"auth_enabled": False, "supabase_url": "", "supabase_anon_key": ""})
    gate_auth_gate(_config(), deps, result, "requested")
    assert result.gates["auth_gate"]["status"] == "SKIPPED"
    assert "not configured" in result.gates["auth_gate"]["details"]["reason"]


def test_auth_gate_passes_when_both_unauthenticated_requests_401(monkeypatch):
    responses = {
        (8080, "/instances|"): (401, "application/json", '{"detail": "Not authenticated"}'),
    }
    result = FrontendCutoverResult()
    deps = FakeDeps(auth_config={"auth_enabled": True, "supabase_url": "x", "supabase_anon_key": "y"}, responses=responses)

    def get(port, path, *, headers=None):
        return (401, "application/json", '{"detail": "Not authenticated"}')

    deps.get = get
    gate_auth_gate(_config(), deps, result, "requested")
    assert result.gates["auth_gate"]["status"] == "PASS"


def test_auth_gate_fails_when_a_bad_token_is_accepted():
    result = FrontendCutoverResult()
    deps = FakeDeps(auth_config={"auth_enabled": True, "supabase_url": "x", "supabase_anon_key": "y"})

    def get(port, path, *, headers=None):
        return (200, "application/json", "[]")  # should have been 401

    deps.get = get
    gate_auth_gate(_config(), deps, result, "requested")
    assert result.gates["auth_gate"]["status"] == "FAIL"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_frontend_cutover_verifier.py -v -k "dev_app_health or web_routes or rsc_payloads or instances_negotiation or auth_gate"`
Expected: FAIL — `gate_dev_app_health` and friends don't exist yet.

- [ ] **Step 3: Implement `RealFrontendDeps` and the five gate functions**

Append to `scripts/verify_frontend_cutover.py`:

```python
import subprocess
import sys
import time
from typing import Any

import httpx

from scripts.verify_lib import OwnedProcess, _pid_started_at


_RSC_PAGES = ("index", "login", "setup", "instances", "stream")


class RealFrontendDeps:
    def __init__(self, config: FrontendCutoverConfig):
        self.config = config
        self._owned_dev_app: OwnedProcess | None = None
        self._owned_installed_app: OwnedProcess | None = None

    def start_dev_app(self, environment: dict[str, str]) -> OwnedProcess:
        app_log = open(self.config.evidence_dir / "dev-app.log", "a", encoding="utf-8")
        no_window = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
        process = subprocess.Popen(
            ["uv", "run", "python", "src/main.py"],
            cwd=self.config.repo_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=app_log,
            stderr=subprocess.STDOUT,
            **no_window,
        )
        app_log.close()
        import psutil

        started_at = psutil.Process(process.pid).create_time()
        self._owned_dev_app = OwnedProcess("dev_app", process.pid, started_at)
        return self._owned_dev_app

    def wait_for_dev_app(self, app: OwnedProcess, port: int) -> bool:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if _pid_started_at(app.pid) != app.started_at:
                return False
            try:
                response = httpx.get(f"http://127.0.0.1:{port}/auth/config", timeout=2)
                if response.status_code == 200:
                    return True
            except httpx.HTTPError:
                pass
            time.sleep(0.5)
        return False

    def auth_config(self, port: int) -> dict[str, Any]:
        response = httpx.get(f"http://127.0.0.1:{port}/auth/config", timeout=5)
        response.raise_for_status()
        return response.json()

    def get(self, port: int, path: str, *, headers: dict[str, str] | None = None) -> tuple[int, str, str]:
        response = httpx.get(f"http://127.0.0.1:{port}{path}", headers=headers or {}, timeout=10)
        return response.status_code, response.headers.get("content-type", ""), response.text

    def terminate(self, process: OwnedProcess) -> None:
        if _pid_started_at(process.pid) != process.started_at:
            return  # already gone, or PID reused by something else
        import psutil

        try:
            psutil.Process(process.pid).terminate()
        except psutil.Error:
            pass


def gate_dev_app_health(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    import os

    environment = dict(os.environ)
    app = deps.start_dev_app(environment)
    if deps.wait_for_dev_app(app, config.port):
        result.mark("dev_app_health", "PASS", reason=reason, details={"pid": app.pid})
    else:
        result.mark("dev_app_health", "FAIL", reason=reason, details={"error": "dev app did not become healthy within the timeout"})


def gate_web_routes(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    observed: dict[str, Any] = {}
    failures: list[str] = []
    for path in ("/", "/login", "/setup", "/stream"):
        status, content_type, _ = deps.get(config.port, path)
        observed[path] = {"status": status, "content_type": content_type}
        if status != 200 or "text/html" not in content_type:
            failures.append(f"{path}: expected 200 text/html, got {status} {content_type!r}")
    if failures:
        result.mark("web_routes", "FAIL", reason=reason, details={"observed": observed, "failures": failures})
    else:
        result.mark("web_routes", "PASS", reason=reason, details={"observed": observed})


_RSC_EXPECTED_CONTENT_TYPES = {
    "manifest.json": "application/json",
    "icon-192.png": "image/png",
    "404.html": "text/html",
}


def gate_rsc_payloads(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    observed: dict[str, Any] = {}
    failures: list[str] = []
    paths = [f"/{page}.txt" for page in _RSC_PAGES] + ["/404.html", "/manifest.json", "/icon-192.png"]
    for path in paths:
        status, content_type, _ = deps.get(config.port, path)
        observed[path] = {"status": status, "content_type": content_type}
        name = path.lstrip("/")
        expected = _RSC_EXPECTED_CONTENT_TYPES.get(name, "text/x-component")
        if status != 200 or expected not in content_type:
            failures.append(f"{path}: expected 200 {expected!r}, got {status} {content_type!r}")
    if failures:
        result.mark("rsc_payloads", "FAIL", reason=reason, details={"observed": observed, "failures": failures})
    else:
        result.mark("rsc_payloads", "PASS", reason=reason, details={"observed": observed})


def gate_instances_negotiation(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    no_header = deps.get(config.port, "/instances")
    json_header = deps.get(config.port, "/instances", headers={"Accept": "application/json"})
    html_header = deps.get(config.port, "/instances", headers={"Accept": "text/html"})
    observed = {
        "no_accept_header": {"status": no_header[0], "content_type": no_header[1]},
        "accept_application_json": {"status": json_header[0], "content_type": json_header[1]},
        "accept_text_html": {"status": html_header[0], "content_type": html_header[1]},
    }
    failures: list[str] = []
    if "application/json" not in no_header[1]:
        failures.append("no-Accept-header request did not return JSON")
    if "application/json" not in json_header[1]:
        failures.append("Accept: application/json request did not return JSON")
    if "text/html" not in html_header[1]:
        failures.append("Accept: text/html request did not return the HTML page shell")
    if failures:
        result.mark("instances_negotiation", "FAIL", reason=reason, details={"observed": observed, "failures": failures})
    else:
        result.mark("instances_negotiation", "PASS", reason=reason, details={"observed": observed})


def gate_auth_gate(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    auth_config = deps.auth_config(config.port)
    if not auth_config.get("auth_enabled"):
        result.mark(
            "auth_gate", "SKIPPED", reason=reason,
            details={"reason": "Supabase auth not configured this run"},
        )
        return
    no_token = deps.get(config.port, "/instances")
    bad_token = deps.get(config.port, "/instances", headers={"Authorization": "Bearer not-a-real-token"})
    observed = {"no_token_status": no_token[0], "bad_token_status": bad_token[0]}
    if no_token[0] == 401 and bad_token[0] == 401:
        result.mark("auth_gate", "PASS", reason=reason, details={"observed": observed})
    else:
        result.mark("auth_gate", "FAIL", reason=reason, details={"observed": observed, "error": "an unauthenticated or garbage-token request to /instances did not get 401"})
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_frontend_cutover_verifier.py -v`
Expected: all tests PASS, including Task 2's earlier tests (still green).

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_frontend_cutover.py tests/test_frontend_cutover_verifier.py
git commit -m "feat: add HTTP-level cutover gates (routes, RSC payloads, auth)"
```

---

### Task 4: `offline_suites` gate — run and parse the project's test suites

**Files:**
- Modify: `scripts/verify_frontend_cutover.py`
- Modify: `tests/test_frontend_cutover_verifier.py`

**Interfaces:**
- Consumes: `FrontendCutoverConfig`, `FrontendCutoverResult` (Task 2).
- Produces (used by Task 6):
  - `RealFrontendDeps.run_command(self, command: list[str], *, cwd: Path | None = None, timeout: float = 600) -> tuple[int, str]` returns `(exit_code, combined_stdout_stderr)`.
  - `def _parse_pytest_summary(output: str) -> dict[str, int]` returns `{"passed": int, "failed": int, "skipped": int, "errors": int}` (any category absent from pytest's summary line is `0`).
  - `def _parse_jest_summary(output: str) -> dict[str, int]` returns `{"passed": int, "failed": int, "total": int}`, parsed from the `Tests:` line jest prints (not the `Test Suites:` line).
  - `def gate_offline_suites(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None`

- [ ] **Step 1: Write the failing tests for the two parsers**

```python
from scripts.verify_frontend_cutover import _parse_jest_summary, _parse_pytest_summary


def test_parse_pytest_summary_extracts_all_four_categories():
    output = "2 failed, 456 passed, 1 skipped, 249 warnings, 2 errors in 25.86s"
    assert _parse_pytest_summary(output) == {"passed": 456, "failed": 2, "skipped": 1, "errors": 2}


def test_parse_pytest_summary_defaults_absent_categories_to_zero():
    output = "45 passed in 3.02s"
    assert _parse_pytest_summary(output) == {"passed": 45, "failed": 0, "skipped": 0, "errors": 0}


def test_parse_jest_summary_extracts_from_the_tests_line_only():
    output = "Test Suites: 1 failed, 9 passed, 10 total\nTests:       2 failed, 43 passed, 45 total\n"
    assert _parse_jest_summary(output) == {"passed": 43, "failed": 2, "total": 45}


def test_parse_jest_summary_handles_zero_failures():
    output = "Test Suites: 10 passed, 10 total\nTests:       45 passed, 45 total\n"
    assert _parse_jest_summary(output) == {"passed": 45, "failed": 0, "total": 45}


def test_gate_offline_suites_passes_when_every_command_exits_zero_with_zero_failures():
    class FakeDepsForSuites:
        def run_command(self, command, *, cwd=None, timeout=600):
            if "pytest" in command:
                return 0, "456 passed in 20s"
            return 0, "Tests:       10 passed, 10 total\n"

    result = FrontendCutoverResult()
    gate_offline_suites(_config(), FakeDepsForSuites(), result, "requested")
    assert result.gates["offline_suites"]["status"] == "PASS"


def test_gate_offline_suites_fails_when_a_suite_reports_failures_even_with_exit_zero():
    class FakeDepsForSuites:
        def run_command(self, command, *, cwd=None, timeout=600):
            if "pytest" in command and "apps/desktop" not in " ".join(command):
                return 0, "2 failed, 456 passed in 20s"
            return 0, "Tests:       10 passed, 10 total\n"

    result = FrontendCutoverResult()
    gate_offline_suites(_config(), FakeDepsForSuites(), result, "requested")
    assert result.gates["offline_suites"]["status"] == "FAIL"


def test_gate_offline_suites_fails_when_a_command_exits_nonzero():
    class FakeDepsForSuites:
        def run_command(self, command, *, cwd=None, timeout=600):
            return 1, "collection error"

    result = FrontendCutoverResult()
    gate_offline_suites(_config(), FakeDepsForSuites(), result, "requested")
    assert result.gates["offline_suites"]["status"] == "FAIL"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_frontend_cutover_verifier.py -v -k "offline_suites or parse_pytest or parse_jest"`
Expected: FAIL — none of these names exist yet.

- [ ] **Step 3: Implement the parsers, `run_command`, and the gate**

Append to `scripts/verify_frontend_cutover.py`:

```python
import re


_PYTEST_CATEGORY_PATTERN = re.compile(r"(\d+) (passed|failed|skipped|error)")


def _parse_pytest_summary(output: str) -> dict[str, int]:
    counts = {"passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    for number, category in _PYTEST_CATEGORY_PATTERN.findall(output):
        key = "errors" if category == "error" else category
        counts[key] = int(number)
    return counts


def _parse_jest_summary(output: str) -> dict[str, int]:
    match = re.search(r"^Tests:\s+(.*)$", output, re.MULTILINE)
    line = match.group(1) if match else ""
    passed_match = re.search(r"(\d+) passed", line)
    failed_match = re.search(r"(\d+) failed", line)
    total_match = re.search(r"(\d+) total", line)
    return {
        "passed": int(passed_match.group(1)) if passed_match else 0,
        "failed": int(failed_match.group(1)) if failed_match else 0,
        "total": int(total_match.group(1)) if total_match else 0,
    }


_SUITE_COMMANDS: tuple[tuple[str, list[str], str], ...] = (
    ("pytest_tests", ["uv", "run", "pytest", "tests/", "-q", "--continue-on-collection-errors"], "pytest"),
    ("pytest_apps_desktop", ["uv", "run", "pytest", "apps/desktop/", "-q"], "pytest"),
    ("jest_core", ["npm", "run", "test:core"], "jest"),
    ("jest_ui", ["npm", "run", "test:ui"], "jest"),
    ("jest_web", ["npm", "test", "-w", "apps/web"], "jest"),
)


def gate_offline_suites(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    details: dict[str, Any] = {}
    any_failed = False
    for name, command, kind in _SUITE_COMMANDS:
        exit_code, output = deps.run_command(command, cwd=config.repo_root, timeout=600)
        counts = _parse_pytest_summary(output) if kind == "pytest" else _parse_jest_summary(output)
        failed = counts.get("failed", 0)
        details[name] = {"exit_code": exit_code, "counts": counts}
        if exit_code != 0 or failed != 0:
            any_failed = True
    if any_failed:
        result.mark("offline_suites", "FAIL", reason=reason, details=details)
    else:
        result.mark("offline_suites", "PASS", reason=reason, details=details)
```

Add `run_command` to `RealFrontendDeps` (from Task 3):

```python
    def run_command(self, command: list[str], *, cwd: Path | None = None, timeout: float = 600) -> tuple[int, str]:
        no_window = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
        try:
            completed = subprocess.run(
                command,
                cwd=cwd or self.config.repo_root,
                text=True,
                capture_output=True,
                timeout=timeout,
                **no_window,
            )
        except subprocess.TimeoutExpired as error:
            return 1, f"timed out after {timeout}s: {error}"
        return completed.returncode, completed.stdout + completed.stderr
```

Note on the two pre-existing documented baseline failures
(`test_windows_verifier.py`'s two failures, and two pre-existing
collection errors): this gate deliberately does NOT hardcode those exact
numbers as an allowed exception — the spec's own reasoning is that a real
Windows run might make those macOS-only failures disappear, and a hardcoded
allowance would hide that improvement or hide a regression equally well.
`gate_offline_suites` reports the raw counts in `details`; a human reading
the evidence (per `docs/WINDOWS_MANUAL_VALIDATION.md` section 10)
judges them against the documented baseline. This is a deliberate, already-decided
design choice from the spec — do not add baseline-matching logic here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_frontend_cutover_verifier.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_frontend_cutover.py tests/test_frontend_cutover_verifier.py
git commit -m "feat: add offline_suites gate with real pytest/jest summary parsing"
```

---

### Task 5: Installer gates — `installed_app_launch`, `frozen_selfrelaunch`

**Files:**
- Modify: `scripts/verify_frontend_cutover.py`
- Modify: `tests/test_frontend_cutover_verifier.py`

**Interfaces:**
- Consumes: `RealFrontendDeps.run_command` (Task 4); `OwnedProcess` (Task 1).
- Produces (used by Task 6):
  - `RealFrontendDeps.start_installed_app(self) -> OwnedProcess` (launches `<install-dir>\WindowControl.exe`, where `<install-dir>` is derived from `config.installer_path`'s sibling install location — see Step 3 for the exact derivation).
  - `RealFrontendDeps.start_selfrelaunch(self, url: str) -> OwnedProcess` (launches `<install-dir>\WindowControl.exe --webview-window <url>`).
  - `def gate_installed_app_launch(config, deps, result, reason) -> None`
  - `def gate_frozen_selfrelaunch(config, deps, result, reason) -> None`

- [ ] **Step 1: Write the failing tests**

```python
def test_installed_app_launch_passes_when_healthy_and_firewall_rule_matches():
    class FakeDepsForInstaller:
        def __init__(self):
            self.started = False

        def start_installed_app(self):
            self.started = True
            return __import__("scripts.verify_lib", fromlist=["OwnedProcess"]).OwnedProcess("installed_app", 5555, 2.0)

        def wait_for_dev_app(self, app, port):
            return True

        def run_command(self, command, *, cwd=None, timeout=600):
            return 0, 'Rule Name: WindowControl-Engine\nProgram: C:\\Program Files\\WindowControl\\_internal\\assets\\engine\\engine.exe\n'

        def terminate(self, process):
            pass

    result = FrontendCutoverResult()
    gate_installed_app_launch(_config(), FakeDepsForInstaller(), result, "requested")
    assert result.gates["installed_app_launch"]["status"] == "PASS"


def test_installed_app_launch_fails_when_firewall_rule_points_elsewhere():
    class FakeDepsForInstaller:
        def start_installed_app(self):
            return __import__("scripts.verify_lib", fromlist=["OwnedProcess"]).OwnedProcess("installed_app", 5555, 2.0)

        def wait_for_dev_app(self, app, port):
            return True

        def run_command(self, command, *, cwd=None, timeout=600):
            return 0, "Rule Name: WindowControl-Engine\nProgram: C:\\some\\stale\\path\\engine.exe\n"

        def terminate(self, process):
            pass

    result = FrontendCutoverResult()
    gate_installed_app_launch(_config(), FakeDepsForInstaller(), result, "requested")
    assert result.gates["installed_app_launch"]["status"] == "FAIL"


def test_frozen_selfrelaunch_passes_when_child_survives_and_parent_still_healthy():
    class FakeDepsForRelaunch:
        def start_selfrelaunch(self, url):
            return __import__("scripts.verify_lib", fromlist=["OwnedProcess"]).OwnedProcess("selfrelaunch", 6666, 3.0)

        def sleep(self, seconds):
            pass

        def process_is_alive(self, process):
            return True

        def wait_for_dev_app(self, app, port):
            return True

        def terminate(self, process):
            pass

    result = FrontendCutoverResult()
    gate_frozen_selfrelaunch(_config(), FakeDepsForRelaunch(), result, "requested", parent=__import__("scripts.verify_lib", fromlist=["OwnedProcess"]).OwnedProcess("installed_app", 5555, 2.0))
    assert result.gates["frozen_selfrelaunch"]["status"] == "PASS"


def test_frozen_selfrelaunch_fails_when_child_process_dies_immediately():
    class FakeDepsForRelaunch:
        def start_selfrelaunch(self, url):
            return __import__("scripts.verify_lib", fromlist=["OwnedProcess"]).OwnedProcess("selfrelaunch", 6666, 3.0)

        def sleep(self, seconds):
            pass

        def process_is_alive(self, process):
            return False

        def wait_for_dev_app(self, app, port):
            return True

        def terminate(self, process):
            pass

    result = FrontendCutoverResult()
    gate_frozen_selfrelaunch(_config(), FakeDepsForRelaunch(), result, "requested", parent=__import__("scripts.verify_lib", fromlist=["OwnedProcess"]).OwnedProcess("installed_app", 5555, 2.0))
    assert result.gates["frozen_selfrelaunch"]["status"] == "FAIL"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_frontend_cutover_verifier.py -v -k "installed_app_launch or frozen_selfrelaunch"`
Expected: FAIL — these names don't exist yet.

- [ ] **Step 3: Implement**

Append to `scripts/verify_frontend_cutover.py`:

```python
def _install_dir(installer_path: Path) -> Path:
    """Real installs from build/installer.iss land at
    C:\\Program Files\\WindowControl regardless of where the .exe installer
    itself sits on disk (installer_path points at the installer artifact,
    not the install destination) -- ArchitecturesInstallIn64BitMode makes
    {autopf} resolve to Program Files, not Program Files (x86).
    """
    import os

    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    return Path(program_files) / "WindowControl"


# Add to RealFrontendDeps:
#
#     def start_installed_app(self) -> OwnedProcess:
#         install_dir = _install_dir(self.config.installer_path)
#         no_window = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
#         process = subprocess.Popen(
#             [str(install_dir / "WindowControl.exe")],
#             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
#             **no_window,
#         )
#         import psutil
#         started_at = psutil.Process(process.pid).create_time()
#         self._owned_installed_app = OwnedProcess("installed_app", process.pid, started_at)
#         return self._owned_installed_app
#
#     def start_selfrelaunch(self, url: str) -> OwnedProcess:
#         install_dir = _install_dir(self.config.installer_path)
#         no_window = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
#         process = subprocess.Popen(
#             [str(install_dir / "WindowControl.exe"), "--webview-window", url],
#             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
#             **no_window,
#         )
#         import psutil
#         started_at = psutil.Process(process.pid).create_time()
#         return OwnedProcess("selfrelaunch", process.pid, started_at)
#
#     def process_is_alive(self, process: OwnedProcess) -> bool:
#         return _pid_started_at(process.pid) == process.started_at


def gate_installed_app_launch(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    app = deps.start_installed_app()
    healthy = deps.wait_for_dev_app(app, config.port)
    exit_code, output = deps.run_command(
        ["netsh", "advfirewall", "firewall", "show", "rule", 'name="WindowControl-Engine"'],
    )
    expected_engine_path = r"WindowControl\_internal\assets\engine\engine.exe"
    firewall_ok = exit_code == 0 and expected_engine_path in output
    details = {"pid": app.pid, "healthy": healthy, "firewall_output": output[:2000], "firewall_ok": firewall_ok}
    if healthy and firewall_ok:
        result.mark("installed_app_launch", "PASS", reason=reason, details=details)
    else:
        result.mark("installed_app_launch", "FAIL", reason=reason, details=details)


def gate_frozen_selfrelaunch(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str, *, parent: OwnedProcess) -> None:
    url = f"http://127.0.0.1:{config.port}"
    child = deps.start_selfrelaunch(url)
    deps.sleep(3)
    child_alive = deps.process_is_alive(child)
    parent_still_healthy = deps.wait_for_dev_app(parent, config.port)
    details = {"child_pid": child.pid, "child_alive": child_alive, "parent_still_healthy": parent_still_healthy}
    deps.terminate(child)
    if child_alive and parent_still_healthy:
        result.mark("frozen_selfrelaunch", "PASS", reason=reason, details=details)
    else:
        result.mark("frozen_selfrelaunch", "FAIL", reason=reason, details=details)
```

Move the three commented `RealFrontendDeps` methods above into the actual
`RealFrontendDeps` class body (they're written as comments here only to
keep this task's diff readable against Task 3's already-written class —
the implementer adds them as real methods, not comments).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_frontend_cutover_verifier.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/verify_frontend_cutover.py tests/test_frontend_cutover_verifier.py
git commit -m "feat: add installed_app_launch and frozen_selfrelaunch gates"
```

---

### Task 6: Manual gates, `run()`, CLI, and `--only`/`--from` wiring

**Files:**
- Modify: `scripts/verify_frontend_cutover.py`
- Modify: `tests/test_frontend_cutover_verifier.py`

**Interfaces:**
- Consumes: everything from Tasks 1-5.
- Produces:
  - `def gate_desktop_shell_visual(config, deps, result, reason) -> None`
  - `def gate_supabase_two_account_flow(config, deps, result, reason) -> None`
  - `def gate_leaked_key_forgery_check(config, deps, result, reason) -> None` (three-way `PASS`/`FAIL`/`SKIP` answer from the operator, per the spec — `SKIP` maps to gate status `SKIPPED`)
  - `RealFrontendDeps.manual_confirm(self, message: str, checkpoint: str) -> str` (uses `CutoverFilePromptChannel` when `config.file_prompts`, else `input()`)
  - `def run(config: FrontendCutoverConfig, deps: Any) -> FrontendCutoverResult` — the full orchestrator.
  - `def main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write the failing tests**

```python
def test_desktop_shell_visual_records_operator_answer():
    class FakeDepsManual:
        def manual_confirm(self, message, checkpoint):
            assert checkpoint == "desktop_shell_visual"
            return "PASS"

    result = FrontendCutoverResult()
    gate_desktop_shell_visual(_config(), FakeDepsManual(), result, "requested")
    assert result.gates["desktop_shell_visual"]["status"] == "PASS"


def test_supabase_two_account_flow_records_fail_answer():
    class FakeDepsManual:
        def manual_confirm(self, message, checkpoint):
            return "FAIL"

    result = FrontendCutoverResult()
    gate_supabase_two_account_flow(_config(), FakeDepsManual(), result, "requested")
    assert result.gates["supabase_two_account_flow"]["status"] == "FAIL"


def test_leaked_key_forgery_check_maps_skip_answer_to_skipped_status():
    class FakeDepsManual:
        def manual_confirm(self, message, checkpoint):
            return "SKIP"

    result = FrontendCutoverResult()
    gate_leaked_key_forgery_check(_config(), FakeDepsManual(), result, "requested")
    assert result.gates["leaked_key_forgery_check"]["status"] == "SKIPPED"


def test_run_executes_every_gate_in_order_on_a_full_run(monkeypatch):
    calls: list[str] = []
    gate_names_called = []

    def make_gate(name):
        def gate(config, deps, result, reason):
            gate_names_called.append(name)
            result.mark(name, "PASS", reason=reason)
        return gate

    import scripts.verify_frontend_cutover as module

    for name in GATE_NAMES:
        monkeypatch.setattr(module, f"gate_{name}", make_gate(name), raising=False)

    result = module.run(_config(), deps=object())
    assert gate_names_called == list(GATE_NAMES)
    assert result.status == "PASS"


def test_run_with_only_skips_unselected_gates_and_caps_incomplete(monkeypatch):
    import scripts.verify_frontend_cutover as module

    def make_gate(name):
        def gate(config, deps, result, reason):
            result.mark(name, "PASS", reason=reason)
        return gate

    for name in GATE_NAMES:
        monkeypatch.setattr(module, f"gate_{name}", make_gate(name), raising=False)

    config = _config(only=("offline_suites",))
    result = module.run(config, deps=object())
    assert result.gates["offline_suites"]["status"] == "PASS"
    assert result.gates["web_routes"]["status"] == "SKIPPED"
    assert result.status == "INCOMPLETE"


def test_run_with_skip_manual_gates_caps_incomplete_even_if_all_pass(monkeypatch):
    import scripts.verify_frontend_cutover as module

    def make_gate(name):
        def gate(config, deps, result, reason):
            result.mark(name, "PASS", reason=reason)
        return gate

    for name in GATE_NAMES:
        monkeypatch.setattr(module, f"gate_{name}", make_gate(name), raising=False)

    config = _config(skip_manual_gates=True)
    result = module.run(config, deps=object())
    assert result.status == "INCOMPLETE"


def test_run_with_skip_installer_skips_exactly_the_three_installer_gates(monkeypatch):
    import scripts.verify_frontend_cutover as module

    def make_gate(name):
        def gate(config, deps, result, reason):
            result.mark(name, "PASS", reason=reason)
        return gate

    for name in GATE_NAMES:
        monkeypatch.setattr(module, f"gate_{name}", make_gate(name), raising=False)

    config = _config(skip_installer=True)
    result = module.run(config, deps=object())
    for name in ("installed_app_launch", "frozen_selfrelaunch", "leaked_key_forgery_check"):
        assert result.gates[name]["status"] == "SKIPPED"
    assert result.status == "INCOMPLETE"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_frontend_cutover_verifier.py -v -k "desktop_shell_visual or supabase_two_account_flow or leaked_key_forgery_check or test_run_"`
Expected: FAIL — `run()` and the three manual gates don't exist yet.

- [ ] **Step 3: Implement the manual gates, `manual_confirm`, `run()`, and `main()`**

Append to `scripts/verify_frontend_cutover.py`:

```python
def gate_desktop_shell_visual(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    answer = deps.manual_confirm(
        "On the machine running the installed app, click the tray's 'Open App' "
        "button. Confirm: a real window opens, shows the login/instance UI (not "
        "blank, not a crash), and clicking 'Open App' again while it's still open "
        "does NOT open a second window.",
        "desktop_shell_visual",
    )
    result.mark("desktop_shell_visual", "PASS" if answer == "PASS" else "FAIL", reason=reason)


def gate_supabase_two_account_flow(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    answer = deps.manual_confirm(
        "Complete the full flow from docs/WINDOWS_MANUAL_VALIDATION.md section 3: "
        "register a new account, confirm empty instance list, confirm the device "
        "claims on first login, confirm a second account cannot see or act on the "
        "first account's claimed instance (403, not silent adoption), confirm "
        "mobile login shows the same linked list as web. PASS only if every part "
        "of this passed.",
        "supabase_two_account_flow",
    )
    result.mark("supabase_two_account_flow", "PASS" if answer == "PASS" else "FAIL", reason=reason)


def gate_leaked_key_forgery_check(config: FrontendCutoverConfig, deps: Any, result: FrontendCutoverResult, reason: str) -> None:
    answer = deps.manual_confirm(
        "If you have a second machine available, complete "
        "docs/WINDOWS_MANUAL_VALIDATION.md section 8 (copy the install's private "
        "key to a second machine, confirm Account B cannot use it to access "
        "Account A's session). If you don't have a second machine for this run, "
        "answer SKIP.",
        "leaked_key_forgery_check",
    )
    if answer == "SKIP":
        result.mark("leaked_key_forgery_check", "SKIPPED", reason=reason, details={"reason": "no second machine available"})
    else:
        result.mark("leaked_key_forgery_check", "PASS" if answer == "PASS" else "FAIL", reason=reason)


# Add to RealFrontendDeps:
#
#     def manual_confirm(self, message: str, checkpoint: str) -> str:
#         if self.config.file_prompts:
#             from scripts.verify_lib import CutoverFilePromptChannel
#             channel = CutoverFilePromptChannel(self.config.evidence_dir, poll_seconds=self.config.file_prompt_poll_seconds)
#             return channel.prompt(message, checkpoint)
#         print(f"CHECKPOINT: {checkpoint}\n{message}")
#         answer = input("PASS/FAIL (or SKIP where offered)? ").strip().upper()
#         return answer


_GATE_FUNCTIONS = {
    "dev_app_health": gate_dev_app_health,
    "web_routes": gate_web_routes,
    "rsc_payloads": gate_rsc_payloads,
    "instances_negotiation": gate_instances_negotiation,
    "auth_gate": gate_auth_gate,
    "offline_suites": gate_offline_suites,
    "installed_app_launch": gate_installed_app_launch,
    "desktop_shell_visual": gate_desktop_shell_visual,
    "supabase_two_account_flow": gate_supabase_two_account_flow,
    "leaked_key_forgery_check": gate_leaked_key_forgery_check,
}


def run(config: FrontendCutoverConfig, deps: Any) -> FrontendCutoverResult:
    selection = resolve_gate_selection(config)
    result = FrontendCutoverResult()
    started_app: OwnedProcess | None = None
    started_installed_app: OwnedProcess | None = None
    try:
        for name in GATE_NAMES:
            reason = selection[name]
            if reason.startswith("not selected"):
                result.mark(name, "SKIPPED", reason=reason)
                continue
            if name == "leaked_key_forgery_check" and config.skip_installer:
                result.mark(name, "SKIPPED", reason="skipped (--skip-installer)")
                continue
            if name in ("installed_app_launch", "frozen_selfrelaunch") and config.skip_installer:
                result.mark(name, "SKIPPED", reason="skipped (--skip-installer)")
                continue
            if name in ("desktop_shell_visual", "supabase_two_account_flow", "leaked_key_forgery_check") and config.skip_manual_gates:
                result.mark(name, "SKIPPED", reason="skipped (--skip-manual-gates)")
                continue
            if name == "dev_app_health":
                gate_dev_app_health(config, deps, result, reason)
                if result.gates[name]["status"] == "PASS":
                    started_app = OwnedProcess("dev_app", result.gates[name]["details"]["pid"], 0)
            elif name == "frozen_selfrelaunch":
                if started_installed_app is not None:
                    gate_frozen_selfrelaunch(config, deps, result, reason, parent=started_installed_app)
                else:
                    result.mark(name, "FAIL", reason=reason, details={"error": "installed_app_launch did not produce a running app to relaunch against"})
            elif name == "installed_app_launch":
                gate_installed_app_launch(config, deps, result, reason)
                if result.gates[name]["status"] == "PASS":
                    started_installed_app = OwnedProcess("installed_app", result.gates[name]["details"]["pid"], 0)
            else:
                _GATE_FUNCTIONS[name](config, deps, result, reason)
    finally:
        if not config.keep_on_failure:
            if started_app is not None:
                deps.terminate(started_app)
            if started_installed_app is not None:
                deps.terminate(started_installed_app)
    payload_extra: dict[str, Any] = {}
    if config.only is not None:
        payload_extra["selection"] = {"mode": "only", "requested": list(config.only)}
    elif config.from_gate is not None:
        payload_extra["selection"] = {"mode": "from", "requested": [config.from_gate]}
    result.selection_extra = payload_extra  # type: ignore[attr-defined]
    return result


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    from scripts.verify_lib import submit_file_confirmation

    parser = argparse.ArgumentParser(description="Verify the frontend/desktop cutover surface")
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path)
    parser.add_argument("--web-build-dir", type=Path)
    parser.add_argument("--installer-path", type=Path)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--file-prompts", action="store_true")
    parser.add_argument("--confirm", choices=("PASS", "FAIL"))
    parser.add_argument("--keep-on-failure", action="store_true")
    parser.add_argument("--skip-manual-gates", action="store_true")
    parser.add_argument("--skip-installer", action="store_true")
    parser.add_argument("--only")
    parser.add_argument("--from-gate", dest="from_gate")
    args = parser.parse_args(argv)

    if args.confirm:
        try:
            path = submit_file_confirmation(args.repo_root, args.confirm, evidence_glob="frontend-cutover-*")
        except Exception as error:  # noqa: BLE001 - mirrors verify_engine_cutover.py's own top-level catch
            print(f"FAIL: {error}")
            return 1
        print(f"Submitted {args.confirm} confirmation to {path.parent.name}")
        return 0

    if not args.evidence_dir or not args.installer_path:
        parser.error("verification requires --evidence-dir and --installer-path")

    config = FrontendCutoverConfig(
        repo_root=args.repo_root,
        evidence_dir=args.evidence_dir,
        web_build_dir=args.web_build_dir or (args.repo_root / "apps" / "web" / "out"),
        installer_path=args.installer_path,
        port=args.port,
        file_prompts=args.file_prompts,
        keep_on_failure=args.keep_on_failure,
        skip_manual_gates=args.skip_manual_gates,
        skip_installer=args.skip_installer,
        only=tuple(args.only.split(",")) if args.only else None,
        from_gate=args.from_gate,
    )
    deps = RealFrontendDeps(config)
    try:
        result = run(config, deps)
    except (FrontendCutoverError, ValueError) as error:
        print(f"FAIL: {error}")
        return 1
    payload = result.to_dict()
    payload.update(getattr(result, "selection_extra", {}))
    from scripts.verify_lib import _write_json_atomic

    _write_json_atomic(config.evidence_dir / "result.json", payload)
    print(json.dumps(payload, indent=2))
    return 0 if result.status == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_frontend_cutover_verifier.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Run the full Python suite for collateral effects**

Run: `uv run pytest tests/ -q --continue-on-collection-errors`
Expected: same documented baseline shape, plus the new test files passing.

- [ ] **Step 6: Commit**

```bash
git add scripts/verify_frontend_cutover.py tests/test_frontend_cutover_verifier.py
git commit -m "feat: wire manual gates, run() orchestrator, and CLI for frontend cutover verifier"
```

---

### Task 7: PowerShell wrapper `engine/verify-frontend-cutover.ps1`

**Files:**
- Create: `engine/verify-frontend-cutover.ps1`

**Interfaces:**
- Consumes: `scripts/verify_frontend_cutover.py`'s CLI (Task 6).
- Produces: the operator-facing entry point, same invocation shape as
  `engine/verify-engine-cutover.ps1`.

- [ ] **Step 1: Write `engine/verify-frontend-cutover.ps1`**

```powershell
# Windows-only frontend/desktop cutover verifier.
# Automates docs/WINDOWS_MANUAL_VALIDATION.md's build/HTTP/process-level
# checks; genuinely manual gates (WebView2 visual confirmation, the
# Supabase two-account browser flow, the leaked-key cross-machine test)
# remain file-prompt confirmations, same mechanism as verify-engine-cutover.ps1.

[CmdletBinding()]
param(
    [string]$WebBuildDir = "",
    [string]$InstallerPath = "",
    [int]$Port = 8080,
    [switch]$KeepOnFailure,
    [switch]$FilePrompts,
    [ValidateSet("", "PASS", "FAIL")]
    [string]$Confirm = "",
    [switch]$SkipManualGates,
    [switch]$SkipInstaller,
    [string]$Only = "",
    [string]$From = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

if ($Only -and $From) {
    throw "-Only and -From are mutually exclusive"
}

if ($Confirm) {
    & uv run python -m scripts.verify_frontend_cutover `
        --repo-root $repoRoot `
        --confirm $Confirm
    exit $LASTEXITCODE
}

if (-not $InstallerPath) {
    $InstallerPath = Join-Path $repoRoot "release\WindowControlInstaller.exe"
}
if (-not $WebBuildDir) {
    $WebBuildDir = Join-Path $repoRoot "apps\web\out"
}
if (-not [System.IO.Path]::IsPathRooted($InstallerPath)) {
    $InstallerPath = Join-Path $repoRoot $InstallerPath
}
if (-not [System.IO.Path]::IsPathRooted($WebBuildDir)) {
    $WebBuildDir = Join-Path $repoRoot $WebBuildDir
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$nonce = [Guid]::NewGuid().ToString("N").Substring(0, 12)
$evidenceDir = Join-Path $repoRoot "engine\test\frontend-cutover-$timestamp-$PID-$nonce"

$arguments = @(
    "run", "python", "-m", "scripts.verify_frontend_cutover",
    "--repo-root", $repoRoot,
    "--evidence-dir", $evidenceDir,
    "--web-build-dir", $WebBuildDir,
    "--installer-path", $InstallerPath,
    "--port", "$Port"
)
if ($KeepOnFailure) { $arguments += "--keep-on-failure" }
if ($FilePrompts) { $arguments += "--file-prompts" }
if ($SkipManualGates) { $arguments += "--skip-manual-gates" }
if ($SkipInstaller) { $arguments += "--skip-installer" }
if ($Only) { $arguments += @("--only", $Only) }
if ($From) { $arguments += @("--from-gate", $From) }

Write-Host "Evidence directory: $evidenceDir"
if ($SkipManualGates) {
    Write-Warning "-SkipManualGates is set: manual gates are auto-answered SKIPPED. This run can never report PASS."
}
if ($SkipInstaller) {
    Write-Warning "-SkipInstaller is set: installer-dependent gates are SKIPPED. This run can never report PASS."
}
if ($Only -or $From) {
    Write-Warning "A partial gate selection (-Only/-From) is set. This run can never report PASS -- use a full run with neither flag for the acceptance record."
}
& uv @arguments
exit $LASTEXITCODE
```

- [ ] **Step 2: Confirm the file is syntactically valid PowerShell**

This macOS/Linux development environment has no PowerShell to execute
this against. Run a syntax-only check instead:

Run: `pwsh -NoProfile -Command "[System.Management.Automation.PSParser]::Tokenize((Get-Content -Raw 'engine/verify-frontend-cutover.ps1'), [ref]$null) | Out-Null; Write-Host OK"` if `pwsh` (PowerShell Core) is available; if not, skip this step and note in the commit message that PowerShell syntax was not locally verified, matching this repo's existing documented limitation for every `.ps1` file (see `HANDOFF.md`'s history of PowerShell-only verification steps).

- [ ] **Step 3: Commit**

```bash
git add engine/verify-frontend-cutover.ps1
git commit -m "feat: add PowerShell entry point for the frontend cutover verifier"
```

---

### Task 8: Documentation and final full-suite verification

**Files:**
- Modify: `docs/WINDOWS_MANUAL_VALIDATION.md`
- Modify: `docs/PROJECT_CONTEXT.md` (Build & verify commands section)

**Interfaces:** none — this task only updates docs and re-verifies, no new code interfaces.

- [ ] **Step 1: Add a pointer from the manual runbook to the new automated tool**

At the top of `docs/WINDOWS_MANUAL_VALIDATION.md` (right after the
"Purpose" paragraph), add:

```markdown
**Automation available:** `engine/verify-frontend-cutover.ps1` automates
every build/HTTP/process-level check below (sections 1, 2, 4's HTTP-only
subset via its own gates, 6, and 7's non-visual parts). It leaves the
same manual gates this document already calls out — desktop-shell visual
confirmation, the Supabase two-account flow, the leaked-key cross-machine
check — as file-prompt confirmations. Run it for a fast pass while
iterating (`-Only <gate>` / `-From <gate>` to target a single gate), and
run it in full (no `-Only`/`-From`) for the acceptance record before
signing off this checklist. This document remains the authoritative
step-by-step reference the tool's manual-gate prompts point back to.
```

- [ ] **Step 2: Add the new commands to `docs/PROJECT_CONTEXT.md`'s Build & verify commands block**

In the `# test` section of the fenced command block, immediately after
the existing `uv run pytest apps/desktop/ -v` line, add:

```
uv run pytest tests/test_verify_lib.py tests/test_frontend_cutover_verifier.py -v  # scripts/, the frontend cutover verifier itself
.\engine\verify-frontend-cutover.ps1 -Only auth_gate       # Windows-only, fast single-gate iteration
.\engine\verify-frontend-cutover.ps1                       # Windows-only, full acceptance run
```

- [ ] **Step 3: Run the complete local suite one final time**

```bash
uv run pytest tests/ -q --continue-on-collection-errors
uv run pytest apps/desktop/ -q
npm run test:core
npm run test:ui
npm test -w apps/web
```

Expected: same documented baseline shape as before this plan, with the
new `test_verify_lib.py` and `test_frontend_cutover_verifier.py` files
passing on top of it.

- [ ] **Step 4: Commit**

```bash
git add docs/WINDOWS_MANUAL_VALIDATION.md docs/PROJECT_CONTEXT.md
git commit -m "docs: point the manual validation runbook at the new automated verifier"
```

---

## Plan self-review

**Spec coverage:** every gate named in the spec (11 gates) has a task
implementing it (Tasks 3-6); the `verify_lib.py` extraction (Task 1), the
`--only`/`--from` selection engine (Task 2), the PowerShell wrapper
(Task 7), and the evidence-format/documentation requirements (Task 6,
Task 8) are all covered. The spec's three "confirm against real code
before writing" open items were resolved while writing this plan (not
deferred to the implementer as guesses): `/auth/config`'s exact response
shape, the five real `.txt` page names, and the real installed-app
directory layout — all now stated as Global Constraints with their source
citations.

**Placeholder scan:** no TBD/TODO markers; every step has real,
runnable code or an exact command. Task 5's `RealFrontendDeps` methods
are written as comments purely to keep that task's diff readable against
Task 3's already-existing class body — the step text explicitly instructs
moving them into the real class, not leaving them as comments in the
final code.

**Type/name consistency:** gate function names (`gate_dev_app_health`,
etc.) match `GATE_NAMES` and `_GATE_FUNCTIONS` exactly across all tasks;
`FrontendCutoverConfig`/`FrontendCutoverResult`'s fields introduced in
Task 2 are used with the same names in every later task
(`config.port`, `config.skip_installer`, `result.mark(...)`, etc.); the
one deliberate exception to "no naming changes" is documented explicitly
in Task 1 (the prompt notice text) and in the Global Constraints (the new
tool's `SKIPPED`/`gates` vocabulary vs. the old tool's `SKIP`/`checkpoints`).

---

Plan complete and saved to `docs/superpowers/plans/2026-09-06-frontend-cutover-verifier.md`. Two execution options:

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
