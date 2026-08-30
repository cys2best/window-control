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
- Windows finding/fix: after the HTTP fix, the offline suite reached the complete fake scrcpy handshake and hung in `ScrcpySource.ConnectInitialSucceedsAndReportsDimensions`; `ScrcpyVideoClient::Stop()` only shut down the socket before joining a reader blocked in `recv()`; commit 1ee0c50 closes the Windows socket before the join (without writing the shared handle until the reader exits) and adds a completed-handshake diagnostic; Windows focused rerun pending
- Finished: expanded `engine/test/README_e2e.md` into the Windows real-device operator runbook
- Next: rebuild on Windows, rerun the focused `ScrcpySource.ConnectInitialSucceedsAndReportsDimensions`, then rerun the corrected full offline suite and finalize through the Superpowers workflow
- Blockers: manual E2E and live signaling are complete; Windows full offline-suite evidence and workflow-owned completion state remain outstanding

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
