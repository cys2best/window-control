# Frontend/Desktop Cutover Verifier — Design Spec

## Context

`2026-09-05-react-unified-frontend` cut the FastAPI backend over to serving
`apps/web`'s Next.js static export, added an `apps/desktop` pywebview shell,
and deleted `src/client/`. Its own SDD review process (task reviews + a
final whole-branch review) caught real bugs at the code level, but nothing
in that plan has ever been exercised on real Windows hardware — the
environment every session that built it ran in (macOS) cannot compile
`engine.exe`, open a pywebview window, or run the PyInstaller installer.

`docs/WINDOWS_MANUAL_VALIDATION.md` is a purely-manual runbook covering this
gap. This spec automates the parts of that runbook that don't require a
human's eyes or a second physical machine, following the existing pattern
established by `scripts/verify_engine_cutover.py` +
`engine/verify-engine-cutover.ps1` (built for `2026-09-01-engine-client-cutover`'s
own Task 11).

## Goals

- Automate every check in `docs/WINDOWS_MANUAL_VALIDATION.md` that is a
  build step, an HTTP probe, or a process/port check.
- Leave every check that requires visual confirmation (WebView2 actually
  rendering) or a second physical machine (leaked-key forgery) as an
  explicit, named manual gate — using the same file-prompt mechanism the
  existing tool already has, not a new one.
- Do not touch the domain logic of `scripts/verify_engine_cutover.py`
  (ADB/browser/WHEP orchestration) — only its shared plumbing.
- Produce the same shape of evidence artifact (`result.json` in a
  timestamped evidence directory) so a human or a future script can compare
  runs the same way.

## Non-goals

- Driving a real browser (Selenium/Playwright) through the Supabase
  auth flow. That flow stays a manual file-prompt gate.
- Running the 8-hour soak or 5-instance performance workloads — out of
  scope for this tool, same as today's accepted overrides.
- Changing anything about how `verify_engine_cutover.py`'s own gates work.

## Architecture

### `scripts/verify_lib.py` (new — extracted, no behavior change)

Pulled out of `scripts/verify_engine_cutover.py`, unmodified in behavior:

- `OwnedProcess` — dataclass wrapping a `subprocess.Popen`, used for
  processes the verifier starts and must clean up.
- `CutoverFilePromptChannel` — writes `active-prompt.json` into the
  evidence dir, nonce/PID/start-time-scoped, polls for a matching
  `prompt-response-<nonce>.json`.
- `submit_file_confirmation(repo_root, result)` — the operator-side
  half, used by `--confirm PASS/FAIL` in a second terminal.
- `_write_json_atomic` / `_read_json` — atomic JSON write (`tmp` +
  `os.replace`) and read-if-exists.
- `_pid_started_at(pid)` — process-identity confirmation (a PID can be
  reused; this pins the same nonce/prompt to a specific process
  instance's start time).
- `_wait_for_health(url, timeout)` — polling GET-until-200 helper,
  generalized from the existing tool's HTTP wait (currently baked into
  its `RealCutoverDeps`).

`scripts/verify_engine_cutover.py` changes to import these five from
`verify_lib` instead of defining its own copies. No other line changes.
Its own 1611-line test suite (`tests/test_engine_cutover_verifier.py`)
re-run unmodified is the acceptance gate for this refactor — green means
the extraction changed nothing observable.

### `scripts/verify_frontend_cutover.py` (new)

Same shape as the existing tool: a `FrontendCutoverConfig` dataclass, a
`FrontendCutoverResult` dataclass (one field per gate below), a
`RealFrontendDeps` class for real subprocess/HTTP/file-prompt operations,
and a `run(config, deps) -> FrontendCutoverResult` pure state machine that
tests drive with a `FakeDeps` double — no real process/network in tests.

CLI (via `argparse`, invoked as `python -m scripts.verify_frontend_cutover`):

```
--repo-root PATH                 (required)
--evidence-dir PATH              (required — PowerShell wrapper generates a timestamped one)
--web-build-dir PATH             (default: <repo-root>/apps/web/out)
--installer-path PATH            (default: <repo-root>/release/WindowControlInstaller.exe)
--file-prompts                   (switch — use the file-prompt channel instead of interactive input())
--confirm {PASS,FAIL}            (operator confirmation from a second terminal; when given, all
                                   other args except --repo-root are ignored, mirrors today's tool)
--skip-manual-gates              (switch — auto-answers manual gates as SKIPPED; caps overall
                                   status at INCOMPLETE, never PASS)
--skip-installer                 (switch — skips installed_app_launch, frozen_selfrelaunch,
                                   and leaked_key_forgery_check; caps overall status at
                                   INCOMPLETE, never PASS)
--port INT                       (default: 8080, matches src/config.py's PORT)
```

**Gates, in the order they run** (each gate's outcome is
`PASS | FAIL | SKIPPED`; a gate failing does not stop later gates —
matches the existing tool's "collect everything, report at the end"
behavior):

1. **`dev_app_health`** — start `uv run python src/main.py` in the repo
   root as an `OwnedProcess`, with `SUPABASE_*` env vars passed through
   from the invoking shell's environment (not hardcoded — the operator
   sets them beforehand per `docs/WINDOWS_MANUAL_VALIDATION.md` section 0)
   and `AUTH_TOKEN` explicitly scrubbed (matches the existing tool's
   environment-sanitization pattern). Poll `GET http://127.0.0.1:<port>/auth/config`
   until 200 or timeout (there is no dedicated `/health` route on the
   FastAPI app — `/auth/config` is always registered and cheap, and its
   response body also tells this gate and gate 5 whether Supabase auth is
   configured in this run).
2. **`web_routes`** — `GET /`, `/login`, `/setup`, `/stream`; assert
   `200` and `content-type: text/html`. Record the actual observed
   status/content-type per path in `details`.
3. **`rsc_payloads`** — `GET /index.txt`, `/login.txt`, `/setup.txt`,
   `/stream.txt`, `/instances.txt`, `/404.html`, `/manifest.json`,
   `/icon-192.png`; assert `200` and the content-type each is expected to
   have (`text/x-component; charset=utf-8` for `.txt`, `application/json`
   for the manifest, `image/png` for the icon, `text/html` for 404).
4. **`instances_negotiation`** — `GET /instances` three ways: no `Accept`
   header (expect the existing JSON-API shape, `application/json`),
   `Accept: application/json` explicit (same), `Accept: text/html` (expect
   `200 text/html`, the page shell). Fails if any of the three doesn't
   match, or if the JSON-shaped requests return HTML (would mean the
   content-negotiation regressed toward always-HTML).
5. **`auth_gate`** — only runs if `dev_app_health`'s `/auth/config`
   response indicates Supabase auth is enabled; otherwise `SKIPPED` with a
   `details` note explaining why (not a silent skip). When it runs: `GET
   /instances` with no `Authorization` header, and again with
   `Authorization: Bearer not-a-real-token`; assert `401` both times.
6. **`offline_suites`** — run, in sequence, capturing exit code and a
   parsed pass/fail/skip count from each command's own output:
   - `uv run pytest tests/ -q --continue-on-collection-errors`
   - `uv run pytest apps/desktop/ -q`
   - `npm run test:core`
   - `npm run test:ui`
   - `npm test -w apps/web`
   Gate passes only if every suite's own exit code is 0 **and** its
   parsed failure count is 0 (a suite that "passes" by exit code but logs
   failures some other way is still a fail here — parse real numbers, not
   just the process return code). The two pre-existing documented
   `test_windows_verifier.py` failures and the two documented collection
   errors do not automatically fail this gate — `details` records them by
   name with a note that they are pre-existing per `HANDOFF.md`, and the
   gate's PASS/FAIL is based on whether the *count* matches that
   documented baseline, not zero. A build that's expected to
   change that baseline (e.g. those macOS-only failures actually passing
   on real Windows) should show as an improvement in `details`, not a
   silent pass-through — record the raw counts either way and let the
   human reading the evidence judge, don't hardcode the exact historical
   numbers as a hard assertion.
7. **`installed_app_launch`** *(skipped entirely if `--skip-installer`)* —
   launch `<installer-path's install dir>\WindowControl.exe` as an
   `OwnedProcess` (installer must already be built and installed by the
   operator per `docs/WINDOWS_MANUAL_VALIDATION.md` section 7 — this tool
   does not run the installer itself, matching the existing tool's
   `--installer-path` convention of taking a pre-built artifact). Poll
   `/auth/config` for health. Then run
   `netsh advfirewall firewall show rule name="WindowControl-Engine"`
   and assert its output contains the real installed `engine.exe` path
   (under that install's `_internal\assets\engine\`).
8. **`frozen_selfrelaunch`** *(skipped if `--skip-installer`)* — with the
   installed app still running from gate 7, invoke
   `WindowControl.exe --webview-window http://127.0.0.1:<port>` directly
   as a second `OwnedProcess`. Assert: the second process starts and
   stays alive for at least N seconds (not an instant crash-exit), the
   original process's health endpoint still responds (proves the child
   didn't rebind port 8080), and no second tray icon process artifact
   appears (best-effort — Windows doesn't make "second tray icon" trivial
   to detect programmatically; if this can't be verified via subprocess
   inspection alone, downgrade this specific assertion to `details` prose
   for the human reviewing evidence, not a hard PASS/FAIL condition — say
   so explicitly in the code comment, not silently).
9. **`desktop_shell_visual`** *(manual, file-prompt)* — message: "On the
   machine running the installed app, click the tray's 'Open App' button.
   Confirm: a real window opens, shows the login/instance UI (not blank,
   not a crash), and clicking 'Open App' again while it's still open does
   NOT open a second window. PASS/FAIL?"
10. **`supabase_two_account_flow`** *(manual, file-prompt, combined per
    your decision)* — message: "Complete the full flow from
    docs/WINDOWS_MANUAL_VALIDATION.md section 3: register a new account,
    confirm empty instance list, confirm the device claims on first
    login, confirm a second account cannot see or act on the first
    account's claimed instance (403, not silent adoption), confirm mobile
    login shows the same linked list as web. PASS only if every part of
    this passed — if any part failed, answer FAIL and describe which part
    when reporting results in HANDOFF.md. PASS/FAIL?" Skipped entirely
    (not asked) if `dev_app_health` determined Supabase auth isn't
    configured for this run.
11. **`leaked_key_forgery_check`** *(manual, file-prompt)* — message:
    "If you have a second machine available, complete
    docs/WINDOWS_MANUAL_VALIDATION.md section 8 (copy the install's
    private key to a second machine, confirm Account B cannot use it to
    access Account A's session). If you don't have a second machine for
    this run, answer SKIP." Three-way answer (`PASS`/`FAIL`/`SKIP`) rather
    than the binary PASS/FAIL every other manual gate uses — this is the
    one gate that has a legitimate "not applicable this run" answer
    distinct from a deliberate skip-everything flag.

**Overall status**: `PASS` only if every gate that wasn't explicitly
skipped (by flag or by "not configured this run") is `PASS`. Any `FAIL`,
or any use of `--skip-manual-gates`/`--skip-installer`, caps overall status
at `FAIL` or `INCOMPLETE` respectively — matches the existing tool's
"can never silently claim full PASS" philosophy exactly.

### `engine/verify-frontend-cutover.ps1` (new)

Same shape as `engine/verify-engine-cutover.ps1`:
- `[CmdletBinding()]` param block mirroring the CLI args above
  (`-WebBuildDir`, `-InstallerPath`, `-FilePrompts`, `-Confirm`,
  `-SkipManualGates`, `-SkipInstaller`, `-Port`).
- Generates the timestamped+nonce evidence dir under
  `engine\test\frontend-cutover-<timestamp>-<PID>-<nonce>`, same naming
  convention.
- Prints `Write-Warning` for every skip flag used, same wording style as
  today's tool ("this run can never report PASS and is not acceptance
  evidence" / "...will not be reported as a measured PASS").
- Delegates to `uv run python -m scripts.verify_frontend_cutover` with
  the assembled argument list; `-Confirm` short-circuits to the
  `--confirm` passthrough exactly like today's script.

## Data flow / evidence format

`<evidence-dir>/result.json`, written atomically via
`verify_lib._write_json_atomic` after every gate (so a crash mid-run still
leaves partial evidence, matching the existing tool):

```json
{
  "status": "PASS | FAIL | INCOMPLETE",
  "started_at": "<iso8601>",
  "finished_at": "<iso8601>",
  "gates": {
    "dev_app_health": {"status": "PASS", "details": {...}},
    "web_routes": {"status": "PASS", "details": {"/": {"status": 200, "content_type": "text/html"}, ...}},
    "rsc_payloads": {"status": "PASS", "details": {...}},
    "instances_negotiation": {"status": "PASS", "details": {...}},
    "auth_gate": {"status": "SKIPPED", "details": {"reason": "Supabase auth not configured this run"}},
    "offline_suites": {"status": "PASS", "details": {"pytest_tests": {"passed": 456, "failed": 2, "note": "2 pre-existing per HANDOFF.md"}, ...}},
    "installed_app_launch": {"status": "PASS", "details": {...}},
    "frozen_selfrelaunch": {"status": "PASS", "details": {...}},
    "desktop_shell_visual": {"status": "PASS", "details": {"operator_confirmed_at": "<iso8601>"}},
    "supabase_two_account_flow": {"status": "PASS", "details": {...}},
    "leaked_key_forgery_check": {"status": "SKIPPED", "details": {"reason": "no second machine available"}}
  }
}
```

## Error handling

- All `OwnedProcess` instances (dev app, installed app, self-relaunch
  child) are cleaned up in a `finally` block — killed on exit unless
  `--keep-on-failure` is passed (new flag, matches existing convention).
- `SUPABASE_*`/`AUTH_TOKEN` env vars are read from the invoking
  environment to configure the dev-app subprocess, but never written into
  `result.json`'s `details` — same sanitization discipline as the
  existing tool's `_sanitized_environment`.
- A gate whose own precondition can't be met (e.g. `installed_app_launch`
  when no installer directory exists) reports `FAIL` with a clear
  `details.reason`, not a crash — the script should never raise an
  unhandled exception partway through; every gate function catches its
  own `OSError`/`subprocess.TimeoutExpired`/`requests` exceptions and
  converts them to a `FAIL` result with the exception message in
  `details`.

## Testing plan

- `tests/test_verify_lib.py` (new) — covers the extracted plumbing
  directly: file-prompt nonce/PID-scoping (a stale prompt from a dead PID
  is rejected, a prompt answered twice is rejected, `_pid_started_at`
  correctly distinguishes a reused PID), atomic JSON write/read
  round-trips, `_wait_for_health` timeout and success paths.
- `tests/test_engine_cutover_verifier.py` (existing, 1611 lines) — re-run
  unmodified after the `verify_lib` extraction; must stay green with zero
  edits to the test file itself (an edit there would mean the extraction
  changed observable behavior, which is out of scope).
- `tests/test_frontend_cutover_verifier.py` (new) — one test per gate's
  pass/fail/skip logic, driven by a `FakeDeps` double (fake HTTP
  responses, fake process handles returning canned exit codes/output,
  fake file-prompt answers), plus tests for: gate failures don't abort
  later gates, `--skip-manual-gates` caps status at INCOMPLETE,
  `--skip-installer` skips exactly gates 7/8/11 and caps at INCOMPLETE,
  `auth_gate` correctly self-skips when `dev_app_health`'s response says
  auth is disabled, environment sanitization actually scrubs
  `SUPABASE_SERVICE_ROLE_KEY`/`AUTH_TOKEN` from what gets recorded.

## Open risks / things the implementer should double check against real code before writing (not guessed here)

- Exact `/auth/config` response shape (whether it has a boolean like
  `"enabled"` this tool can key gate 5's skip logic off, or whether that
  has to be inferred from `SUPABASE_URL` env var presence instead — check
  `src/server/app.py:421` directly).
- Whether `apps/web/out`'s actual `.txt` files include one for
  `/setup.txt` and `/stream.txt` (assumed yes per Task 10's report, but
  confirm against a real build before hardcoding the list).
- The real installed-app directory layout under `--installer-path`'s
  install location, to construct the path to `WindowControl.exe` and
  `_internal\assets\engine\engine.exe` for gate 7/8 — read
  `build/installer.iss` and `build/window_control.spec` directly rather
  than assuming the layout described in this spec is exact.
