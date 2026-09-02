# Handoff Log

<!-- agent-sync:handoff-template:start -->
<!-- Newest entry on top. Each agent appends one entry at session end,
     and one "Claiming" line when picking up a task. Multiple plans can
     appear here at once — always include the plan name. -->

Plan/task identifiers belong here and in workflow state, not in commit subjects.

## Template for new entries
```
### YYYY-MM-DD HH:MM — [claude|codex]
- Claiming: plan-name/task-N (if starting new work)
- Finished: plan-name/task-N, other-plan/task-M
- Next: plan-name/task-K is ready, depends on plan-name/task-N
- Blockers: none / describe
```
<!-- agent-sync:handoff-template:end -->

---

### 2026-09-02 20:25 — codex
- Claiming: 2026-09-01-engine-client-cutover/task-11 WSS acceptance fix
- Ruling: the Windows Host failure is a confirmed production defect, not an
  endpoint typo: `wss://signaling.koeeru.com` reaches the non-TLS
  `websocketpp::config::asio_client`, which rejects secure endpoints before
  connecting. Implement TLS client support test-first; `ws://` is not an
  acceptable public-path workaround.
- Next: implement, Windows-build, and independently review secure WSS engine
  signaling, then repeat the affected C++ and final cutover gates.
- Blockers: macOS cannot compile/run the C++ change; Host-PC WSS and complete
  acceptance evidence remains required.

### 2026-09-02 09:30 — codex
- Finished: 2026-09-01-engine-client-cutover/task-11 implementation and
  review/fix lifecycle (commits `ad0871b..8344e45..89e5004..7f7ed34`), plus a
  final whole-branch review and its single approved fix wave. Fresh local
  evidence: 211 focused Python passed, 45 browser Node passed, 62 mobile Jest
  passed with TypeScript clean, 11 signaling Node passed, compilation and
  whitespace checks passed.
- Next: run Task 11's complete Windows Host acceptance command: unfiltered
  engine C++ suite behind the Node relay, followed by
  `engine/verify-engine-cutover.ps1` with five real devices, local/public
  browser/VPS, bearer-auth mobile, installer/firewall, tray cleanup, and an
  eight-hour soak. Preserve the final JSON/evidence; any skip or shortened
  soak is INCOMPLETE.
- Blockers: macOS has no CMake/Windows runtime and cannot establish C++,
  PowerShell, installer, firewall, real-device/VPS, or eight-hour-soak PASS.
  The full Python baseline still stops only on two documented unrelated
  collection errors (`test_auto_unlock.py`, `test_window_manager.py`);
  performance is recorded as owner-OVERRIDDEN, not measured PASS.

### 2026-09-01 23:20 — codex
- Claiming: 2026-09-01-engine-client-cutover/task-11
- Next: implement and independently review the final peer-observability and direct-cutover verification task through the managed SDD workflow.
- Blockers: complete Windows/device/8-hour soak acceptance must be run on the Windows Host PC; macOS can only establish automated tooling evidence.

### 2026-09-01 23:10 — claude
- Finished: 2026-09-01-engine-client-cutover/task-9 (commits
  `3429e8d..8f18823..439458b`; fix round 1 removed the retained Android MJPEG
  capture pipeline that Task 9's first commit had left running as a second
  media backend; re-review clean), 2026-09-01-engine-client-cutover/task-10
  (commits `439458b..068dbbd..f987bba`; packages the engine-only PyInstaller
  build, installer-owned engine firewall rule, unfiltered `engine_tests.exe`
  in CI behind a real Node relay; fix round 1 removed a leftover
  `--gtest_filter` on the live-signaling suites in
  `scripts/verify_python_orchestration.py`; re-review clean)
- Next: Task 11 (final task) — expose `local_peers`/`public_peer` on
  `/admin/health`, add `scripts/verify_engine_cutover.py` +
  `engine/verify-engine-cutover.ps1` final direct-cutover matrix. Touches
  `engine/src/*.cpp` and needs a Windows C++ build/compile step this macOS
  session cannot do reliably — stopped here per owner request so they can
  review Tasks 9-10 before deciding how to dispatch Task 11 and before any
  final whole-branch review.
- Blockers: none for Tasks 9-10 (both review-clean); Task 11 needs Windows
  Host PC or `build-engine` CI for its C++ portion, same as every other
  `engine/` task in this repo.

### 2026-09-01 20:45 — codex
- Claiming: 2026-09-01-engine-client-cutover/task-8
- Next: resume the interrupted managed mobile WHEP/DataChannel cutover from the
  existing Task 8 worktree changes; preserve the required task report/review
  lifecycle.
- Blockers: none identified; device smoke remains a later physical-device gate.

### 2026-09-01 22:35 — codex
- Claiming: 2026-09-01-engine-client-cutover/task-9
- Ruling: execute the destructive engine-only legacy removal under the recorded
  owner `OVERRIDE CUTOVER`; it explicitly supersedes Task 9's otherwise
  required measurement-hash artifact. If wrong, performance regression remains
  unmeasured and rollback relies on the prior installer.
- Next: implement, verify, and independently review the mandatory engine
  orchestration cutover.
- Blockers: Windows-engine and device acceptance cannot be proven on macOS.

### 2026-09-01 20:35 — codex
- Finished: 2026-09-01-engine-client-cutover/task-6 review fix round 1 (commit
  `e4d6e20`): manager reserves a ready replacement before predecessor WHEP
  cleanup, browser requested/adopted identity is separated, and settings/stats
  controls are restored.
- Evidence: 42/42 Node client tests and 34/34 required Python tests passed;
  JavaScript syntax and whitespace checks passed.
- Next: Task 6 is ready for scoped re-review.
- Blockers: real Safari/iOS and Windows-engine runtime interoperability remain
  acceptance gates.

### 2026-09-01 20:15 — codex
- Finished: 2026-09-01-engine-client-cutover/task-6 (commit `cd4844e`): browser
  selection/reconnect now fetches fresh engine metadata, accepts only the
  current session generation, and routes gestures/keys/IDR/echo through the
  ready engine DataChannel; removed browser input WebSocket, WHEP prewarm/probe,
  renegotiation, and MJPEG fallback behavior.
- Evidence: final Node VM/client suite passed 38/38; required Python app/config
  suite passed 34/34 (89 documented FastAPI/TestClient deprecation warnings);
  JS syntax and whitespace checks passed.
- Next: review Task 6, then Task 9 may consume the browser input cutover when
  its other prerequisites are complete.
- Blockers: real browser/Safari/iOS and Windows-engine interoperability remains
  a runtime acceptance gate.

### 2026-09-01 19:22 — codex
- Claiming: 2026-09-01-engine-client-cutover/task-6
- Finished: task-5 final server selection/auth contract at `80b52a2..66a634e`;
  fresh focused verification passed 167/167, with documented pre-existing
  FastAPI/TestClient deprecation warnings
- Next: integrate browser UI/pointer behavior with owned engine sessions and
  the reliable DataChannel sender
- Blockers: Windows/browser/device interoperability remains a later runtime gate

### 2026-09-01 19:35 — codex
- Finished: 2026-09-01-engine-client-cutover/task-5 review fix round 1:
  engine child processes drop `AUTH_TOKEN`, and fixed-clock WHEP/viewer
  credentials are distinct while retaining C++/relay-valid signed formats.
- Next: Task 5 is ready for scoped re-review; Task 6 may consume its contract
- Blockers: Windows/browser/device interoperability remains a later runtime gate

### 2026-09-01 19:20 — codex
- Finished: 2026-09-01-engine-client-cutover/task-5 (final engine selection,
  native bearer auth, HTTP-only public tunnel, and removed staging/server input
  routes)
- Next: task-6 may integrate browser UI with the published selection contract
- Blockers: Windows/browser/device interoperability remains a later runtime gate

### 2026-09-01 19:00 — codex
- Claiming: 2026-09-01-engine-client-cutover/task-5
- Finished: task-4 browser local/public session ownership at `bf52843..2201b7c`;
  fresh Node verification passed 31/31 after reviewed lifecycle repairs
- Next: publish and test the final FastAPI selection/auth contract and remove
  server-side input routes
- Blockers: browser/device interoperability remains a later runtime gate

### 2026-09-01 18:58 — codex
- Finished: 2026-09-01-engine-client-cutover/task-4 review fix round 1 (commit `2201b7c`): adoption remains cancellable during async cleanup, premature post-answer signaling close fails, setup throws cleanly fall back, and readiness strictly requires a video track.
- Evidence: exact RED had 5 new lifecycle regressions; fresh final Node production-artifact suite passed 31/31, with syntax and post-commit whitespace checks clean.
- Next: Task 4 is ready for scoped re-review; Task 6 may consume its browser modules after acceptance.
- Blockers: real browser/device interoperability remains a later integration gate.

### 2026-09-01 18:51 — codex
- Finished: 2026-09-01-engine-client-cutover/task-4 (commit `bf52843`): browser-owned local WHEP and public raw-SDP sessions now create the ordered input channel before each offer, race to full video/ICE/channel readiness, and clean transport-specific resources.
- Evidence: Task 4 RED recorded missing artifact and audio-track readiness failures; final Node production-artifact verification passed 25/25, JavaScript syntax and post-commit whitespace checks passed.
- Next: Task 6 can integrate the tested session/input modules with the existing browser UI and pointer layer.
- Blockers: no Task 4 implementation blocker; real browser/device interoperability remains a later integration gate.

### 2026-09-01 18:42 — codex
- Claiming: 2026-09-01-engine-client-cutover/task-4
- Finished: task-3 browser reliable motion sender at `a22da86`; fresh Node
  verification passed 9/9 and the scoped review was clean
- Next: implement and review browser-owned local/public engine sessions
- Blockers: none for Task 4; Task 2 Windows C++ verification remains pending

### 2026-09-01 18:23 — codex
- Claiming: 2026-09-01-engine-client-cutover/task-3
- Next: implement the browser reliable ordered-motion sender for the canonical
  engine input protocol published by Task 2
- Blockers: Task 2 Windows C++ compile/runtime verification remains pending on
  the Host PC or CI

### 2026-09-01 18:20 — codex
- Finished: 2026-09-01-engine-client-cutover/task-2 (commit `9d17395`): the
  engine now rejects malformed/missing/non-finite-equivalent input fields,
  clamps finite coordinates, emits proportional bounded scroll swipes, and
  retains one-time peer-local drag cancellation; real negotiated-transport
  tests cover the contract
- Evidence: `git diff --check` and post-commit `git show --check` passed;
  Windows RED/GREEN and the complete offline engine suite are unrun because
  this macOS checkout has no CMake, PowerShell, or Windows build artifact
- Next: run the exact Task 2 Windows commands on the Host PC/CI before relying
  on C++ runtime evidence; Task 3 can consume the canonical protocol afterward
- Blockers: Windows C++ compile/runtime verification only

### 2026-09-01 18:14 — codex
- Claiming: 2026-09-01-engine-client-cutover/task-2
- Next: implement and independently review engine drag/scroll semantics before
  the browser/mobile sender tasks consume that canonical protocol
- Blockers: Windows C++ build/runtime verification must occur on the Host PC or
  CI; macOS cannot establish that evidence

### 2026-09-01 18:12 — codex
- Finished: 2026-09-01-engine-client-cutover/task-1 measurement tooling and
  two reviewed repair rounds (commits `59c60ef`, `2b44dec`, `c0f45c4`);
  same-origin authenticated viewer selection, readiness, per-snapshot process
  aggregation, schema validation, and bounded diagnostics are covered
- Evidence: fresh controller verification passed 85/85
  `test_measure_engine_cutover.py` + `test_windows_verifier.py` tests;
  `git diff --check 90bbc3a..c0f45c4` passed; the live five-instance workload
  remains intentionally waived by the owner’s recorded override
- Next: Task 2 is ready, but stop here per owner direction
- Blockers: none for implementation; no Windows performance comparison exists
  by explicit owner decision

### 2026-09-01 17:59 — codex
- Ruling: owner explicitly authorized `OVERRIDE CUTOVER: skip five-instance
  validation; proceed with engine-only cutover`; the omitted evidence is an
  accepted risk, and the final verifier must label the performance gate
  overridden rather than measured PASS
- Next: complete the local Task 1 code-quality repair/review, then continue
  direct-cutover implementation; no Windows performance workload blocks Task 5
  or Task 9 under this override
- Blockers: none for implementation; Windows/device acceptance remains needed
  for real runtime claims other than the explicitly waived performance gate

### 2026-09-01 16:18 — codex
- Claiming: 2026-09-01-engine-client-cutover/task-1
- Next: implement and review the measurement tooling; the four live Windows
  workloads and owner APPROVE/OVERRIDE decision remain a hard predecessor to
  Task 5's staging-route removal and Task 9's legacy deletion
- Blockers: Windows Host PC evidence and owner decision are required later;
  local tooling implementation can proceed now

### 2026-09-01 15:46 — codex
- Finished: created and self-reviewed the next implementation phase,
  `2026-09-01-engine-client-cutover`, from the approved full-migration design;
  it specifies the direct browser/mobile cutover, reliable DataChannel gesture
  coalescing, legacy deletion, packaging, and final acceptance matrix
- Next: execute Task 1 through the Superpowers plan workflow; Tasks 2–8 may run
  before the performance decision, but Task 9 legacy deletion requires the
  recorded five-instance APPROVE/OVERRIDE artifact
- Blockers: none for planning; implementation needs the Windows Host PC and an
  explicit owner performance decision before the irreversible deletion task

### 2026-09-01 13:50 — codex
- Finished: 2026-08-31-engine-python-orchestration/task-9 and plan acceptance
  (commits 220a3d9..90bbc3a; final whole-plan review clean after one fix wave)
- Evidence: final Windows Host raw result is PASS across all eight gates:
  discovery, live/expiry peers, quality reconnect, scrcpy recovery, engine
  respawn, emulator removal, and tray Exit with no owned engine or selected
  forward; local evidence is 201/201 phase tests and 11/11 relay tests, with
  the full suite retaining only its three documented unrelated collection errors
- Next: decide how to integrate feature/engine
- Blockers: none

### 2026-09-01 12:09 — codex
- Final whole-plan review found and its single fix wave closed three Important
  defects (commits c556b23..90bbc3a; scoped re-review clean): auth-enabled
  signaling now requires a finite numeric expiry claim, engine shutdown
  tombstones the registry against late starts, and a failed selected scrcpy
  launch cleans its exact Android server/forward before raising
- Evidence: fresh controller runs passed 201/201 Python phase tests with 113
  documented deprecation warnings, 11/11 relay tests, compilation, whitespace,
  and dependency/C++ scope checks; the full Python baseline remains exactly the
  three documented unrelated collection errors
- Next: update the Windows Host checkout through 90bbc3a and repeat the full
  Task 9 file-prompted verifier; the prior green matrix predates this final
  lifecycle/auth fix wave and cannot close the plan
- Blockers: one final Windows result with all eight matrix gates plus tray Exit
  must be recorded before plan completion

### 2026-09-01 11:31 — codex
- Windows evidence: Task 9 now passes discovery, selection, independent
  expiry peer, quality reconnect, scrcpy recovery, engine respawn, and selected
  emulator removal. The final application-exit gate did not run because the
  operator response was not `PASS`; the runner intentionally retained the app
  and owned engine for diagnosis
- Next: exit the retained app from its tray, restart the disposable emulator,
  and rerun the complete file-prompted verifier; at the final prompt exit via
  the tray first, then submit `-Confirm PASS` from terminal 2
- Blockers: only a recorded PASS for actual tray Exit plus the runner's
  app/engine/forward cleanup checks remains before Task 9 can be completed

### 2026-09-01 11:19 — codex
- Finished: Task 9 verification-driven watchdog cleanup correction (commits
  08aec74, 1038d87; independent review and scoped re-review clean): each
  engine-mode watchdog tick now performs serialized ADB discovery before the
  health sweep, allowing a removed selected serial to invoke the existing
  `remove_instance()` lifecycle. Regression coverage spans watchdog → real
  orchestrator → runtime, blocks an in-flight recovery, and proves the engine,
  selected forward, and registry are cleaned up
- Evidence: fresh controller runs passed 5/5 focused lifecycle tests, 252/252
  Task 9 phase tests (113 documented deprecation warnings), 9/9 signaling
  tests, Python compilation, whitespace, and dependency/C++ scope checks;
  full Python collection remains exactly the three documented unrelated errors
- Next: update the Windows checkout through 1038d87, restart only LDPlayer
  index 0, then rerun the full file-prompted verifier and return its final JSON
- Blockers: Windows removal-during-recovery and app-exit gates remain the only
  unmet Task 9 acceptance evidence; this is not Task 9 completion

### 2026-09-01 11:10 — codex
- Claiming: 2026-08-31-engine-python-orchestration/task-9
  (post-removal engine cleanup regression)
- Evidence: the Windows run passed discovery through engine-respawn and shut
  down the selected LDPlayer, but timed out waiting for its engine cleanup;
  static trace shows the engine watchdog calls `check_all()` without a device
  discovery refresh, so it never observes the removed serial and never calls
  `remove_instance()`
- Next: add a red regression test for watchdog-driven removal, implement the
  smallest discovery-refresh correction, review it, and rerun the Windows
  matrix
- Blockers: real Windows rerun required after the correction

### 2026-09-01 08:31 — codex
- Finished: Task 9 supplemental selected-LDPlayer removal correction for
  2026-08-31-engine-python-orchestration (commits a9b0104, 82b6018;
  independent scoped review clean after one fix round): emulator removal now
  resolves LDConsole through the production search path and quits only the
  discovered LDPlayer index; unavailable, failed, timed-out, false-success,
  and process-spawn failures enter the selected-device-only manual fallback;
  Windows launches are hidden and diagnostics are bounded/redacted
- Evidence: fresh controller runs passed 57/57 focused tests, 254/254 phase
  tests with 113 pre-existing warnings, and 9/9 signaling tests; Python
  compilation, whitespace, dependency-lock, and C++ scope checks passed
- Next: update the Windows checkout through 82b6018, restart only LDPlayer
  index 0, rerun
  `.\\engine\\verify-python-orchestration.ps1 -Serial emulator-5554 -FilePrompts`,
  and return the final JSON/evidence after the emulator-removal and app-exit
  gates
- Blockers: Windows integration of the corrected removal path and the final
  app-exit checkpoint remain unverified; no Task 9 or plan completion is
  claimed

### 2026-09-01 — codex
- Claiming: 2026-08-31-engine-python-orchestration/task-9 (LDPlayer removal fix round 1)
- Finished: LDPlayer removal fix round 1 (commit 82b6018): resolved
  LDConsole spawn failures now become bounded/redacted VerificationError
  diagnostics for the selected-device manual fallback, and Windows LDConsole
  quit uses CREATE_NO_WINDOW while preserving the selected index/timeout/capture
  boundary
- Evidence: 5/5 RED/GREEN boundary tests, 57 focused verifier/window-list
  tests, 250 phase tests, 9/9 signaling tests, compilation/whitespace and
  dependency/C++ scope checks passed; full Python baseline remains exactly the
  three documented collection errors
- Next: rerun the Windows removal and later Task 9 matrix checkpoints from the
  Host PC, then review retained evidence before claiming Task 9 complete
- Blockers: Windows integration remains unverified; unrelated pre-existing
  dirty files remain untouched

### 2026-09-01 01:28 — codex
- Finished: Task 9 supplemental selected-scrcpy-process recovery correction for
  2026-08-31-engine-python-orchestration (commits 9ae90aa, 2f927d1;
  independent review clean after one fix round): production pre-launch/stop,
  verifier recovery, and the E2E runbook now target only the observed Android
  `com.genymobile.scrcpy.Server ... scid=<hex>` process; CleanUp and other
  scids are excluded, and verifier kill failures surface immediately with
  bounded/redacted diagnostics
- Evidence: Windows proved the old pattern was a no-op because live health
  stayed generation 1/connected at 480x272; fresh controller runs passed
  49/49 focused tests, 242/242 phase tests with 113 pre-existing warnings,
  and 9/9 signaling tests; compilation, whitespace, dependency, and C++ scope
  checks passed
- Next: exit the retained WindowControl tray process, update Windows through
  2f927d1, rerun
  `.\engine\verify-python-orchestration.ps1 -Serial emulator-5554 -FilePrompts`,
  answer each checklist from terminal 2 with
  `.\engine\verify-python-orchestration.ps1 -Confirm PASS` (or `FAIL`), and
  return the generated result/evidence
- Blockers: real Windows scrcpy recovery and all later matrix checkpoints are
  still unverified; no Task 9 or whole-plan completion is claimed

### 2026-09-01 00:41 — codex
- Finished: Task 9 supplemental device-aware quality verification correction
  for 2026-08-31-engine-python-orchestration (commits 5e34448, 3f52f5d;
  independent review clean after one fix round): the verifier establishes the
  requested baseline tier before its first selection, then switches `480` to
  `720` or any other accepted baseline to `480`, preserving the required
  generation/dimension, WHEP endpoint, PID, and peer-continuity gates
- Evidence: the Windows failure proved reconnect succeeded (`generation 0 ->
  1`) while an upward `1080` ceiling left native `960x544` dimensions unchanged;
  fresh controller runs passed 41/41 verifier tests and 237/237 phase tests
  with 113 pre-existing warnings, plus Python compilation, whitespace,
  dependency, and C++ scope checks
- Next: exit the retained WindowControl tray process on Windows, update through
  3f52f5d, then rerun
  `.\engine\verify-python-orchestration.ps1 -Serial emulator-5554 -FilePrompts`
  and answer each checklist from terminal 2 with
  `.\engine\verify-python-orchestration.ps1 -Confirm PASS` (or `FAIL`)
- Blockers: quality and every later Windows matrix checkpoint remain unverified;
  no Task 9 or whole-plan completion is claimed yet

### 2026-09-01 00:03 — codex
- Finished: Task 9 supplemental quality-route and operator-checklist fixes for
  2026-08-31-engine-python-orchestration (commits c8b3fb5, e165066;
  independent review clean): Python now calls the C++ engine's exact
  `/admin/health`, `/admin/reconnect`, and `/admin/keyframe` routes, real HTTP
  contract tests cover those paths, and every manual/file confirmation prints
  its full observable checklist
- Verification: fresh controller run passed 233/233 phase tests with 113
  pre-existing deprecation warnings; Python compilation, whitespace,
  dependency scope, and C++ scope checks passed; focused reviewer run passed
  56/56 tests
- Next: update the Windows checkout through e165066, ensure only the selected
  emulator is running, then rerun terminal 1 with
  `.\engine\verify-python-orchestration.ps1 -Serial emulator-5554 -FilePrompts`;
  answer each displayed checklist from terminal 2 with
  `.\engine\verify-python-orchestration.ps1 -Confirm PASS` (or `FAIL`) and
  return the generated result/evidence
- Blockers: the Windows matrix from the quality gate onward remains unverified;
  no Task 9 or whole-plan completion is claimed yet

### 2026-08-31 22:03 — codex
- Finished: Task 9 supplemental two-terminal confirmation tooling for
  2026-08-31-engine-python-orchestration (commits 05da5bc, bf0cf90; scoped
  review clean after one fix round): `-FilePrompts` bypasses GUI-hosted stdin,
  `-Confirm PASS|FAIL` publishes nonce-isolated responses, expiry waits report
  progress, prompt cleanup cannot block helper teardown, and the single-device
  safety condition is checked both before tests and immediately before discovery
- Ruling: rejected the initial review's literal-`KILL` blocker because the
  current runner contains no `KILL` comparison; scrcpy/engine termination is
  automatic after preceding PASS gates. Three real Important findings (late
  response race, cleanup exception ordering, stale device preflight) were fixed
  in bf0cf90 and the scoped re-review found all addressed with no new breakage
- Verification: fresh controller runs passed 35/35 focused verifier tests and
  230/230 phase tests with the same 113 non-blocking deprecation warnings;
  Python compilation, whitespace, dependency, C++ scope, credential, stderr,
  and selection-time WHEP-mint checks are clean
- Next: on Windows, manually close every LDPlayer except `emulator-5554`, verify
  `adb devices` has exactly one `device`, then run terminal 1:
  `.\engine\verify-python-orchestration.ps1 -Serial emulator-5554 -FilePrompts`;
  for each requested decision run terminal 2:
  `.\engine\verify-python-orchestration.ps1 -Confirm PASS`; return the generated
  evidence directory/result for Task 9 review
- Blockers: PowerShell execution and all eight real-engine/manual-device matrix
  items remain unverified; do not dispatch final whole-branch review yet

### 2026-08-31 18:36 — codex
- Finished: Task 9 Windows-verifier startup isolation for
  2026-08-31-engine-python-orchestration (commits 79f49b2, f69a29a; scoped
  review clean at the 5/5 tooling-round cap): verifier children now receive
  one sanitized environment, transient connection refusal is retried only
  during discovery while the owned app lives, and preflight refuses only this
  repository's exact retained `src/main.py` process without writing evidence
- Verification: fresh 23/23 focused verifier tests and 218/218 phase tests
  passed with the same 113 non-blocking warnings; Python compilation,
  whitespace, dependency, and C++ scope checks passed
- Next: on the Windows Host PC, first exit the retained WindowControl process
  from its tray, update through f69a29a, then run from repository root:
  `.\engine\verify-python-orchestration.ps1 -Serial emulator-5554`; return
  the generated `engine\test\verification-<timestamp>` evidence directory
- Blockers: Task 9 and final whole-branch review remain gated on a green
  Windows integration rerun; no Windows success is claimed yet

### 2026-08-31 16:43 — codex
- Finished: Task 9 verification-driven standalone-runner import fix for
  2026-08-31-engine-python-orchestration (commit 68ea114, independent review
  clean): `RealDeps` now bootstraps `<repo-root>\src` before importing the
  application discovery module, so the one-command verifier no longer depends
  on pytest or an inherited `PYTHONPATH`
- Verification: the exact standalone reproduction passes with `PYTHONPATH`
  removed; 15/15 focused verifier tests and 210/210 phase tests passed with
  the same 113 non-blocking warnings; cwd-divergence review probe also passed
- Next: update the Windows checkout through 68ea114 and rerun from repository
  root: `.\engine\verify-python-orchestration.ps1`; return the generated
  `engine\test\verification-<timestamp>` evidence directory
- Blockers: Task 9 and final whole-branch review remain gated on a green
  Windows integration rerun; no Windows success is claimed yet

### 2026-08-31 16:28 — codex
- Finished: verification-driven Windows portability fix for
  2026-08-31-engine-python-orchestration/Task 9 (commits 7ede89b, 166899d;
  review clean after 1 fix round): readiness now reaps an EOF-closed child
  within the existing startup deadline before reporting its exit code, and
  shutdown keeps both deterministic ordering coverage and a real `Popen`
  hard-kill/reap check with the normal five-second budget
- Verification: 13/13 focused process tests and 209/209 phase tests passed;
  the Windows run that triggered the fix had 191 passes and two failures, so
  its one-command matrix result is superseded and must be rerun
- Next: on the Windows Host PC, pull commits through 166899d and rerun from
  repository root: `.\engine\verify-python-orchestration.ps1`; return the
  generated `engine\test\verification-<timestamp>` evidence directory
- Blockers: Task 9 and final whole-branch review remain gated on a green
  Windows rerun; no Windows integration success is claimed yet

### 2026-08-31 15:58 — codex
- Finished: supplemental one-command Windows verifier for
  2026-08-31-engine-python-orchestration/Task 9 (commits 2b33584..539b0ef;
  review clean after 2 fix rounds): `engine/verify-python-orchestration.ps1`
  delegates to a behavior-tested Python state machine, serves a token-aware
  browser verifier, validates process/generation/WHEP/forward invariants, and
  writes timestamped evidence
- Verification: fresh 14/14 verifier behavior tests and 207/207 phase tests
  passed; Python compilation and diff whitespace checks passed; existing 113
  FastAPI/Starlette deprecation warnings remain non-blocking
- Next: on the Windows Host PC, run from repository root:
  `.\engine\verify-python-orchestration.ps1`; return the resulting
  `engine\test\verification-<timestamp>` directory for evidence review and
  managed Task 9 completion
- Blockers: the eight-item Windows real-engine matrix is still unverified;
  final whole-branch review/branch finishing remains gated on that run

### 2026-08-31 15:08 — codex
- Finished: 2026-08-31-engine-python-orchestration Tasks 5–8 through the
  superpowers:subagent-driven-development review loop; Task 5 fixed false
  recovery after post-reconnect health failure (23527ef), Task 6 added/reviewed
  the orchestration registry and selection coverage (1377574, 36960ff), Task 7
  wired discovery/routes and fixed overlapping-refresh plus pending-tier defects
  (e7435f0, 0f11f93), Task 8 added reviewed startup configuration/construction
  (593158f)
- Finished: Task 9 local gates — 193 focused Python tests, 9/9 Node signaling
  tests, exactly the three documented full-suite collection errors, and clean
  static/scope checks; no Task 9 fix or commit needed
- Next: run Task 9's eight-item real-engine matrix on the Windows Host PC and
  record evidence in the managed SDD report; only after it is green, complete
  Task 9, dispatch the final whole-branch review, and use the branch-finishing
  workflow
- Blockers: Windows Host PC integration matrix unavailable in this Darwin
  session; plan remains incomplete and final review must not be dispatched

### 2026-08-31 13:15 — codex
- Claiming: 2026-08-31-engine-python-orchestration (resuming the existing
  superpowers:subagent-driven-development lifecycle at Task 5's task-review
  gate on feature/engine, in place per the human partner's prior choice)
- Next: complete Task 5 review, then Tasks 6–9 and the final whole-branch review
- Blockers: none; Task 9 still requires the Windows Host PC integration matrix

### 2026-08-31 13:14 — claude
- Claiming: 2026-08-31-engine-python-orchestration (executing via
  superpowers:subagent-driven-development, in place on feature/engine, no
  worktree per human partner's choice)
- Finished: Task 1 (scrcpy-server launcher extraction, commits
  eaea097; 1 fix round — deleted a leftover duplicate `_start_server` body
  that shadowed the new alias and silently broke legacy
  `ScrcpySession.start()`), Task 2 (validated engine subprocess + ready-record
  boundary, commits da82a5b; 1 fix round — closed a bool-in-place-of-int test
  gap, the guard itself was already correct), Task 3 (WHEP/signaling
  credentials + VPS relay role enforcement, commits 2403883; 1 ruling — the
  brief's own literal test asserted an engine-role signaling token using the
  viewer TTL, contradicting the brief's documented 7-day engine TTL
  requirement; ruled prose wins, fixed `EngineTokenIssuer.signaling()` to
  branch TTL by role; also fixed a real security gap in
  `infra/vps/signaling/server.js` — JWT verification previously checked only
  `session`, not `role`, letting a viewer-scoped token connect as
  `role=engine`; removed the relay's message-queueing mechanism per spec, now
  live-delivery-only), Task 4 (typed loopback admin client + fake admin
  server, commits a7accd3; review clean, 4 minor findings deferred — a
  bool-as-int gap in health/reconnect field validation, same bug class as
  Task 2 but lower severity here), Task 5 (serialized per-instance
  `EngineRuntime`, commits a642424; 1 major ruling — the brief's own two
  mandated tests demanded mutually exclusive `base_generation` rules for
  `_reconnect_locked` under identical runtime state; ruled base_generation
  must always come from the engine's own reported health generation, never a
  Python-side launch counter, verified against the real, already-Windows-
  verified `engine/src/scrcpy_source.cpp:145-149` strict-greater reconnect
  contract; task review of this ruling's implementation in progress at
  session end)
- Next: Task 5's task review (dispatched on Opus, checking the ruling was
  applied consistently at every `_reconnect_locked` call site, not just the
  two directly-tested ones) has not yet returned a verdict — pick up the SDD
  loop from there. Tasks 6 (EngineOrchestrator discovery registry), 7
  (InstanceManager/app.py wiring), 8 (config/main.py construction), and 9
  (cross-component + Windows integration gate) remain undispatched. Full
  ruling list and pre-flight scan live in
  `.superpowers/sdd/2026-08-31-engine-python-orchestration/progress.md` —
  read it before resuming, do not re-derive from git log alone.
- Blockers: none — all rulings so far were resolvable without stopping;
  Task 9's Windows integration matrix will need the Windows Host PC, same as
  every other plan in this repo.

### 2026-08-31 09:15 — codex
- Finished: verification-driven rewrite of `2026-08-31-engine-python-orchestration`; replaced the socket-conflicting/lazy/cached-token design with a socket-free scrcpy launcher, discovery-time per-instance runtime, serialized reconnect and respawn lifecycle, fresh selection credentials, relay role/offline-drop enforcement, exact focused tests, baseline comparison, and Windows integration gate
- Next: human review of the revised phase plan; after approval, execute it only through `superpowers:subagent-driven-development` or `superpowers:executing-plans`
- Blockers: none for planning; implementation has not started

### 2026-08-31 02:16 — codex
- Claiming: 2026-08-30-engine-core-rewrite/task-10-browser-sdp-interoperability-fix (authorized from the live manual gate)
- Finished: browser SDP interoperability implementation (commits b0f31b3, 18e910d) — answerer now preserves offered m-line/MID order, selects an offered H264 payload type, uses the offered track, and explicitly sequences the v0.22.4 answer instead of triggering a second `actpass` offer; browser-shaped regression added; all engine C++ offer fixtures now use pinned-version APIs and manual negotiation
- Windows finding/fix: first focused run reached 19 tests but exposed `Illegal role actpass in remote answer description` plus offer fixtures negotiated before adding video; commit 18e910d fixes both; human-reported Windows rerun passed all focused `PeerSession.*:WhepHandler.*:InputRouter.*` tests
- Windows finding/fix: manual launcher run exposed Windows PowerShell 5 turning merged native stderr into terminating `NativeCommandError`; commit bf29108 makes engine diagnostics non-terminating, removes `2>&1` from the runbook, and prints the separate static-page server URL
- Windows finding/fix: reconnect instructions assumed window-local variables survived; empty serial/port/scid/tier produced Python syntax failure and a rejected null-port JSON request without changing generation 0; commit dbc059f makes the block self-contained, typed, and passes values as Python argv
- Windows finding/correction: cleanup commands failed because `$serial` and `$scid` were unset in that PowerShell window, shifting `adb -s` arguments so `--remove`, `pkill`, and `--list` appeared to be unsupported commands; commit 6441eb6 replaces the earlier vendor-ADB diagnosis in 35ebc34 with a self-contained, validated cleanup block
- Finished: Task 10 real-device manual E2E gate — first peer rendered non-black live video with climbing `framesDecoded`, open input DataChannel, and working click; two independent tabs remained live; corrected generation reconnect kept the same engine/tab alive, returned `accepted: true` for generation 1, and health reported generation 1 `connected` at 720x408; engine printed `Stopped.` on `Ctrl+C`; cleanup left no `emulator-5554` forward while preserving other emulator forwards
- Windows finding/fix: generation reconnect ordering was not explicit enough; commit 4ff209f now states that window A and its WHEP/admin ports must remain alive while only the scrcpy server is replaced, and that an engine restart is a new run rather than reconnect evidence
- Windows finding/fix: live signaling tests received messages from unexpected peers and lost a claimed role because they reused fixed session IDs against a long-lived relay; commit 0a76922 gives all `SignalingClient`/`PublicSignalingBridge` integration tests collision-resistant per-process sessions, state-based connection waits, atomic callback counters, and payload diagnostics; relay Node suite passes 6/6 locally
- Windows environment fix: Host PC has no Node/npm; commit 7ca12a6 adds an auth-free, loopback-only Python signaling relay using the repository's existing `websockets` dependency, plus Windows instructions and 3/3 passing relay contract tests; the unrelated full Python suite still has its pre-existing collection errors for removed `auto_unlock`, `input_handler`, and `window_manager` APIs
- Windows verification: human-reported rebuilt `SignalingClient.*:PublicSignalingBridge.*` live-relay suite passes in full against the Python relay after commits 0a76922 and 7ca12a6
- Documentation finding/fix: the offline GTest filter had a second leading minus, which GTest treats as part of the second negative pattern rather than another separator; commit e968d46 uses `-SignalingClient.*:PublicSignalingBridge.*` so both live suites are actually excluded
- Windows finding/fix: the corrected offline suite hung at `EngineHttpServer.LoopbackBindRefusesNonLoopbackByAddressChoice`; pinned cpp-httplib 0.19.0 makes `stop()` a no-op before `listen_after_bind()` sets its running flag, so immediate `Start()`/`Stop()` could miss shutdown and join forever; commit dfa93b6 waits on the library readiness barrier before returning from `Start()`
- Windows finding/fix: after the HTTP fix, the offline suite reached the complete fake scrcpy handshake and hung in `ScrcpySource.ConnectInitialSucceedsAndReportsDimensions`; `ScrcpyVideoClient::Stop()` only shut down the socket before joining a reader blocked in `recv()`; commit 1ee0c50 closes the Windows socket before the join (without writing the shared handle until the reader exits) and adds a completed-handshake diagnostic
- Windows verification: human-reported the focused scrcpy source case passes after 1ee0c50 and the corrected full offline suite passes all 81 tests; together with the passing live-signaling suite and real-device manual E2E gate, every plan-required Windows acceptance gate now has green evidence
- Finished: expanded `engine/test/README_e2e.md` into the Windows real-device operator runbook
- Next: finalize plan completion through the Superpowers workflow
- Blockers: implementation and acceptance evidence are complete, but this Codex environment does not expose the required `superpowers:subagent-driven-development` or `superpowers:executing-plans` completion command, so workflow-owned progress/report state remains unfinalized and must not be hand-edited

### 2026-08-31 00:30 — codex
- Claiming: 2026-08-30-engine-core-rewrite/final-whole-branch-review-fix
- Claiming: 2026-08-30-engine-core-rewrite/final-review-residual-fix (explicitly authorized follow-up)
- Finished: single final-review fix wave (commits ddb65b5..440d705); all original C/I findings addressed; portable suite 15/15 green
- Finished: authorized residual fix wave (commits 440d705..5e96e85); focused re-review clean, whole-branch code review clean; fresh portable suite 16/16 green
- Next: run the plan's Windows build/full suite/live-signaling tests and Task 10 manual real-device e2e gate
- Blockers: Windows build/test and manual e2e remain unavailable in this macOS session

### 2026-08-31 00:00 — claude
- Claiming: 2026-08-30-engine-core-rewrite/task-6
- Claiming: 2026-08-30-engine-core-rewrite/task-7
- Claiming: 2026-08-30-engine-core-rewrite/task-8
- Claiming: 2026-08-30-engine-core-rewrite/task-9
- Claiming: 2026-08-30-engine-core-rewrite/task-10 (code portion only)
- Finished: 2026-08-30-engine-core-rewrite/task-6 (commits 455f185..72ba248,
  review clean, spec ✅, quality Approved, 3 minors deferred to ledger)
- Finished: 2026-08-30-engine-core-rewrite/task-7 (commits 72ba248..cc6d7ab,
  review clean, spec ✅, quality Approved; 1 Important finding parked with
  ruling — Public-peer eviction-before-validation DoS traced to Task 3's
  already-approved PeerRegistry::Create contract, not a Task 7 defect;
  production requires JWT auth on this path per spec; 2 minors deferred)
- Finished: 2026-08-30-engine-core-rewrite/task-8 (commits cc6d7ab..67fdd3a,
  review clean, spec ✅, quality Approved; named UAF risk on InputRouter's
  raw-pointer-keyed finger-state map traced and cleared; 2 minors deferred)
- Finished: 2026-08-30-engine-core-rewrite/task-9 (commits 67fdd3a..922e0dd,
  review clean on opus, spec ✅, quality Approved; verified the
  plan-acknowledged InputRouter-wiring gap is closed correctly in both
  WhepHandler and PublicSignalingBridge; 5 minors deferred)
- Finished: 2026-08-30-engine-core-rewrite/task-10 code portion (Steps
  1-3+6 only; commits 922e0dd..a5df2ae..ddb65b5, review clean after 1 fix
  round — test.ps1's unwired -WhepPort param fixed to read the real port
  from the engine's ready record)
- Next: all 10 tasks' code is now implemented and individually reviewed
  clean. Final whole-plan review dispatched (opus, range 3e204b5..ddb65b5,
  this plan's own 16 commits) — in progress. After that: Task 10's Step 4
  (Windows build + full engine_tests.exe suite) and Step 5 (manual e2e
  gate: real scrcpy-server + browser + device) CANNOT run in this session
  (no Windows hardware) — a session on the Windows Host PC must run both
  before this plan is truly complete.
- Blockers: Windows-only build/test verification (all 10 tasks'
  accumulated gap, plus Task 10's own manual e2e gate) remains deferred to
  the Host PC or build-engine CI, per plan's environment note

### 2026-08-30 18:40 — codex
- Claiming: 2026-08-30-engine-core-rewrite/task-3
- Claiming: 2026-08-30-engine-core-rewrite/task-4
- Claiming: 2026-08-30-engine-core-rewrite/task-5
- Finished: 2026-08-30-engine-core-rewrite/task-3, 2026-08-30-engine-core-rewrite/task-4, 2026-08-30-engine-core-rewrite/task-5
- Next: 2026-08-30-engine-core-rewrite/task-6 is ready; resume through the Superpowers SDD workflow
- Blockers: Windows-only build/test verification remains deferred to the Host PC or build-engine CI

### 2026-08-30 17:20 — codex
- Finished: architecture verification and revision of
  `2026-08-30-engine-full-migration-design`; resolved the VPS signaling,
  WHEP/DataChannel, multi-peer lifecycle, auth/admin, reconnect, packaging,
  CI, and cutover-gate gaps; no implementation plan or tasks generated
- Next: human review of the revised design spec
- Blockers: none

### 2026-08-30 16:35 — claude
- Finished: 2026-08-29-cpp-engine-sps-pps-cache fully verified on Windows
  Host PC — engine_tests.exe all green (focused H264Nalu.*/SpsPpsCache.*
  filter and full suite excluding SignalingClient.*), plus the manual e2e
  gate confirming the black-frame bug is fixed. Plan complete.
- Next: feature/engine ready to merge (currently kept as-is, not yet
  merged into feature/aiortc per human partner's choice).
- Blockers: none.

### 2026-08-30 16:20 — claude
- Finished: 2026-08-29-cpp-engine-sps-pps-cache manual e2e gate (plan's Task 2
  Step 5) — confirmed on Windows Host PC: engine.exe streams the device
  screen correctly through the SPS/PPS cache fix, black-frame bug resolved.
- Next: engine_tests.exe (GTest unit suite, `--gtest_filter="H264Nalu.*:SpsPpsCache.*"`
  then full suite excluding `SignalingClient.*`) has not been run yet — do
  that before considering the plan fully verified, then merge feature/engine.
- Blockers: none.

### 2026-08-30 15:40 — claude
- Finished: 2026-08-29-cpp-engine-sps-pps-cache/task-1, 2026-08-29-cpp-engine-sps-pps-cache/task-2
- Next: 2026-08-29-cpp-engine-sps-pps-cache is implementation-complete on branch feature/engine; Windows-side verification (engine_tests.exe, the manual e2e gate in the plan's Task 2 Step 5) still required before merge
- Blockers: none — verification blocked only by lack of Windows hardware in this session
