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

### 2026-09-05 22:42 — claude
- Finished: 2026-09-05-react-unified-frontend/task-10 (final task) and the
  plan's own final whole-branch review, via superpowers:subagent-driven-development.
  Task 10 itself (commit 4437d8c) had been implemented and committed by an
  earlier session; this session resumed it, dispatched the task-10 reviewer
  that earlier session never got a result back from, ran one fix round
  (commit 8800659), a scoped re-review confirming both fixes independently,
  then the plan's final whole-branch review (commit range 1a53f08..8800659,
  20 commits). Both reviews came back clean.
- Ruling (real, found during task-10's own review, not hypothetical): the
  original implementation had 2 Critical bugs it had reported as safe —
  (1) `webview.start()` was called from a `threading.Thread` in
  `src/main.py`; the installed pywebview 6.2.1 raises unconditionally off
  the main thread, so "Open App" could never actually open a window; (2)
  Next's static-export client router fetches `<path>.txt` RSC payloads for
  soft navigation, the server served none of them, so the app's default
  post-login destination (`router.replace("/instances")`) silently
  hard-navigated onto the pre-existing JSON `GET /instances` API route
  instead of the page shell — the exact opposite of what the report and
  `docs/PROJECT_CONTEXT.md`'s Decisions log had claimed. Fixed: pywebview
  now runs in a separate subprocess (`apps/desktop/webview_main.py`);
  FastAPI now serves the `.txt` RSC payloads and content-negotiates
  `GET /instances` (HTML vs JSON) via one shared `_prefers_html` predicate
  used identically by the auth gate and the handler, so they can't diverge.
  Also fixed in the same round: dropped PWA manifest/viewport-lock/
  apple-mobile-web-app meta tags (real regression for a touch-streaming
  client) were restored via `apps/web`'s Next metadata/viewport exports.
  Both fixes independently re-verified by a scoped re-review (ran a real
  child-process thread-identity probe, grepped the real built export
  rather than trusting the report).
- Verified (final whole-branch review, sonnet — opus hit a session rate
  limit mid-review and was re-dispatched): 0 Critical, 0 Important across
  the whole plan. Specifically re-checked the auth-gate composition risk
  this repo has a documented incident for — confirmed `_prefers_html`
  is one shared predicate, not two lookalikes, and the claim-once-then-lock
  ownership logic from 2026-09-04-public-session-isolation has zero diff
  across this entire plan's range. `src/client`/`tests/client` deletion
  confirmed complete, no dangling references anywhere. CI wiring for
  `packages/core`/`packages/ui`/`apps/web` confirmed pinned by real test
  assertions, not just documented commands.
- Next: this plan is fully complete. Real Windows hardware verification of
  the pywebview desktop shell (the "Open App" flow, the frozen-build
  `--webview-window` self-reinvoke path, and the overall PyInstaller
  build) remains outstanding — same shape as every other `apps/desktop`/
  `engine/`-touching plan in this repo, not blocking this plan's own
  closure. Proceeding to superpowers:finishing-a-development-branch.
- Blockers: none for this plan. Windows manual verification is the
  standing follow-up, as usual.

---

### 2026-09-04 21:45 — claude
- Finished: 2026-09-04-public-session-isolation (all 9 code tasks +
  final whole-branch review + one fix wave, executed via
  superpowers:subagent-driven-development from spec
  docs/superpowers/specs/2026-09-04-public-session-isolation-design.md,
  plan docs/superpowers/plans/2026-09-04-public-session-isolation.md,
  commits 1d60f11..ed0104d on feature/engine). Replaces the shared,
  copyable HMAC secret that authorized public-relay signaling (which
  both let any two installs collide on the same session name AND let
  anyone who knew the secret forge access to any install) with
  account-verified access: viewers authenticate to the VPS relay with
  their own real Supabase login (JWKS-verified, same ES256 check
  `auth.py` already does), engines authenticate with a per-install
  Ed25519 keypair whose public half is registered to the owning account
  in a new `installs` table. Sessions are now `{owner_user_id}.
  {instance_name}`. Also removed the unrelated `device_links`
  per-instance-linking mechanism (this app is one-owner-per-install, so
  per-instance ACLs inside one PC's own list didn't apply) — no client
  ever called its link/unlink routes anyway.
- Ruling (real, found during the final whole-branch review — not
  hypothetical): removing `device_links` had left *no* ownership check
  anywhere. Any self-registered Supabase account in the project could
  seize an install's identity with one authenticated `GET /instances`
  (the new upsert-on-login adopted whichever account authenticated most
  recently), locking out the real owner and taking over the next engine
  respawn — reopening the exact class of hole this plan exists to close.
  Fixed: ownership now claims once (trust-on-first-use) then locks — a
  different account gets `403`, never silently adopted. Switching an
  already-claimed install's owner now requires local filesystem access
  (delete `install_owner.txt`), matching the design's own stated threat
  model. Independently re-verified by a second review pass (traced the
  conditional by hand, confirmed the 403 path can't fire for the
  matching owner or for unauthenticated/exempt requests).
- Ruling (also found in the same final review, also real — not
  hypothetical): Task 7's client-side rename broke
  `tests/client/engine_session.test.js` from 25/25 to 14/25, and nobody
  noticed because that suite is wired into no script/CI (only documented
  in `docs/PROJECT_CONTEXT.md`'s build/verify block, run by convention).
  Fixed and restored to 25/25; recommend adding a real npm/CI entry for
  it — did not do so beyond documenting the command, to keep this fix
  wave bounded.
- Verified (myself, independently, not trusting agent reports): all 4
  suites after the fix wave — `uv run pytest tests/ -v`: 432 passed / 2
  pre-existing unrelated `test_windows_verifier.py` failures / 1 skipped
  / 2 pre-existing collection errors (`test_auto_unlock.py`,
  `test_window_manager.py`); `node --test tests/client/engine_session.test.js`:
  25/25; `npm test` in `infra/vps/signaling/`: 18/18; `npm test` in
  `mobile/`: 67/67. `engine/` (C++, Task 6) could not be compiled or
  tested from this session — never built on macOS per
  `engine/BUILD_WINDOWS.md` — verified only by careful manual reading
  (twice, by two different reviewers) of brace balance and the actual
  `SignalingClient` constructor signature.
- Next: **Task 10 (this plan's own final task) is a manual checklist for
  a human on real Windows hardware with a real Supabase project** — not
  something any session could execute. It covers: applying
  `infra/supabase/installs.sql` (and manually dropping the now-unused
  `device_links` table if a project still has it), redeploying the VPS
  relay with the new `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` env vars
  and the `jose` dependency, building the Windows engine and running its
  offline `engine_tests.exe` suite, then a multi-PC/same-account gate, an
  account-switch gate, and a leaked-private-key sanity check (copy
  `install_key.bin` to a different machine, confirm it can only ever
  squat on the *original* PC's own session, never forge a different
  account's). Full details in the plan's Task 10.
- Blockers: none for closing the code side of this plan; Task 10's
  manual/real-hardware verification is required before calling the
  whole plan done, same shape as every other `engine/`-touching plan in
  this repo. Also open, not blocking: `scripts/verify_engine_cutover.py`
  (tooling from the already-closed 2026-09-01-engine-client-cutover
  plan) still can't complete a real run against Supabase-auth-enabled
  mode at all — it authenticates via the old shared `AUTH_TOKEN` scheme,
  which predates and is incompatible with Supabase JWT auth (a gap from
  2026-09-03-supabase-multi-user-auth, not from this plan). This plan's
  own Task 10 doesn't use that script for exactly this reason. A handful
  of Minor findings were parked with rulings in this plan's own SDD
  ledger (now deleted per the workflow's normal cleanup, since git
  history is the record) — recommended follow-up, not blocking: memoize
  the install keypair at module level (currently loaded independently at
  two call sites, `main.py` and `app.py`, which could diverge if a
  corrupt key sits in an unwritable directory), and self-heal a
  regenerated key by re-running the idempotent `upsert_install` on a
  request from the already-matching owner.

---

### 2026-09-04 19:30 — claude
- Finished: owner confirmed on the Windows Host PC that Supabase auth
  now works end-to-end after both fixes below (530c83b ES256/JWKS,
  f4a38a6 audience claim) — login succeeds and `/instances` no longer
  401s. Both were root-caused from real evidence (decoded JWT header,
  then a direct local repro script), not guessed.
- Discovered (real gap, not a bug in what exists): there is no UI
  anywhere — web, mobile, or desktop tray — that calls
  `POST /instances/{id}/link`. `GET /instances` only ever returns
  instances already linked to the caller, so a freshly-registered user
  has no way to discover or claim an unlinked device through the app
  itself; the backend route from
  2026-09-03-supabase-multi-user-auth exists and is tested, but nothing
  client-side was ever wired to it. Gave the owner a DevTools-console
  workaround (`window.wcFetch('/instances/adb:<serial>/link', {method:
  'POST'})`, confirmed id format is `adb:<serial>` from
  `src/server/adb_manager.py:207`) to keep testing unblocked; did not
  build the real "claim this instance" UI this session — offered, not
  yet decided.
- Next:
  1. Owner to finish the manual Supabase gate now that auth actually
     works: confirm linked instance appears in the list, confirm a
     second account does NOT see it, confirm mobile login shows the
     same linked list as web. If that passes,
     2026-09-01-engine-client-cutover's public/mobile gap (recorded
     13:50 below) can close for real — rerun
     `verify-engine-cutover.ps1` WITHOUT `-SkipPublicMobile` once ready.
  2. Decide on and build the missing "claim/link instance" UI (real
     feature work — probably needs a new endpoint to list
     discovered-but-unclaimed instances, since `/instances` filters
     those out entirely for an authed caller).
  3. The user explicitly asked to merge feature/engine into main and
     delete feature/authenticate + feature/engine — started via
     superpowers:finishing-a-development-branch, paused before any
     merge/delete happened (nothing destructive done) when priorities
     shifted to Supabase testing. Found along the way:
     origin/feature/authenticate has one commit
     (a3a8d3, a HANDOFF-only doc update) not reachable from
     feature/engine — confirmed pure duplicate content of what's
     already in feature/engine's own HANDOFF, safe to lose when that
     branch is deleted. origin/main has also moved independently
     (b870c9c, unrelated WHEP-prewarm work) since feature/engine
     forked, so the merge needs a fresh `git pull origin main` first,
     not a fast-forward assumption. Still needs: full test suite on
     the merged result, then `git branch -d`/`git push --delete` for
     both branches, per the skill's normal flow.
  4. Also still open: the parked legacy-route id-format mismatch from
     the 2026-09-03-supabase merge (POST /select, GET /windows
     403-everyone under auth — fails closed, no client calls them, but
     unresolved); soak's decode-stall root cause (13:50 below).
- Blockers: none of the above are blocking — all are queued follow-ups
  for whoever picks this up next.

### 2026-09-04 18:00 — claude
- Ruling: real Supabase testing (owner creating a fresh project to close
  the public/mobile gap left open in the 13:50 entry below) found login
  succeeding but every subsequent authenticated request 401ing
  ("Not authenticated") with a correctly-copied Legacy JWT Secret.
  Root-caused by decoding the issued token's header (`{"alg":"ES256",
  "kid":"..."}`)): this Supabase project signs access tokens with an
  asymmetric key (ES256, Supabase's current default), not the legacy
  shared HS256 secret `auth.py` was written against. No correct secret
  value could ever have passed that check — an algorithm mismatch, not
  a config error, and not something the 2026-09-03-supabase-multi-user-auth
  plan's own test suite caught (its hand-rolled JWT test fixtures always
  self-signed HS256, so they never exercised what a real Supabase
  project actually issues).
- Finished: replaced the HS256/shared-secret check with PyJWT's
  PyJWKClient (fetches + caches Supabase's public JWKS, verifies ES256,
  refetches only on an unrecognized kid) — commit 530c83b.
  SUPABASE_JWT_SECRET is gone entirely, not just unused: asymmetric
  verification needs a public key, not a shared secret. TDD: rewrote
  tests/test_auth.py and tests/test_app_auth.py to sign real ES256
  tokens with an in-test EC key pair and stub only the network JWKS
  fetch, so the actual verification logic runs for real in every test,
  not just against self-consistent fake HS256 tokens. 433 passed / 2
  pre-existing unrelated failures (env-var pollution) / 1 skipped.
- Next: owner to pull and retest the full Supabase manual gate (register
  → empty list → link → appears; second account isolation; mobile same
  list as web). If that passes, 2026-09-01-engine-client-cutover's
  public/mobile gap (recorded 13:50 below) can close for real without
  `-SkipPublicMobile`.
- Blockers: none for the fix itself; unverified until the owner reruns
  the manual gate with this commit live.

### 2026-09-04 13:50 — claude
- Finished: 2026-09-01-engine-client-cutover/task-11 — final Windows Host
  PC run with commit c0015fe. Real PASS on every gate that can run
  without a Supabase project: local browser, local/public race, rapid
  switches (20/20), quality ladder, scrcpy recovery, engine recovery,
  installer (64-bit path/firewall rule/uninstall cleanup), tray exit.
  Overall `status: INCOMPLETE`, from exactly three intentional
  non-PASS markers, nothing else:
  - `performance`: OVERRIDDEN — pre-existing recorded owner decision
    (2026-09-01 17:59 entry, before archival), five-instance workload
    never measured.
  - `soak`: OVERRIDDEN — the 8h run failed once (decode-stall bug,
    frames_decoded plateaued for a minute mid-run, connection stayed
    alive, no black frames); accepted as a known risk via
    `--soak-override` rather than re-run. Root cause never
    investigated (blocked on: exact stall sample index in
    `soak-samples.json`, `app.log`/`verification.log` around that
    timestamp, possible correlation with the ICE-disconnected
    grace-period fix in `7f9b712`).
  - `public browser` + `mobile`: SKIP via `--skip-public-mobile` — zero
    evidence this session. Blocked on a real Supabase project
    (SUPABASE_URL/ANON_KEY/JWT_SECRET/SERVICE_ROLE_KEY) and applying
    `infra/supabase/device_links.sql`, neither done yet.
- Ruling: owner accepted this as closing 2026-09-01-engine-client-cutover
  with those two gaps explicitly recorded (soak: known bug, public/mobile:
  unconfigured Supabase), rather than requiring either before closing.
- Finished this session, en route to the above: fixed a chain of real
  bugs surfaced only by actually running the Windows matrix (not
  guessable from macOS) — see commits a36192c, fd9da08, 8fa5596,
  93c3282, b5cb61d, 21870a9, 5c87b18, 774918e, 16209eb, c0015fe.
  Notably: installer.iss was missing 64-bit mode (silently installed
  to `Program Files (x86)`), the engine firewall allow-rule pointed at
  a path that never existed under PyInstaller 6's `_internal\` layout
  (a real pre-existing production bug, not just test tooling), and the
  verifier's own console-spawn/dotenv/env-sanitization gaps (found via
  real screenshots and tracebacks from the Host PC, not guessed).
- Next: if/when a Supabase project gets configured, rerun without
  `-SkipPublicMobile` to close that gap for real. Soak's decode-stall
  root cause remains open — revisit if it recurs in production. The
  parked legacy-route id-format mismatch from the 2026-09-03-supabase
  merge (POST /select, GET /windows under auth) is still unresolved
  too.
- Blockers: none for closing as-is; both remaining gaps require
  explicit follow-up work (Supabase project setup; soak bug
  investigation) whenever someone picks them up.

### 2026-09-04 02:10 — claude
- Finished: 2026-09-03-supabase-multi-user-auth (all 10 tasks, executed
  via superpowers:subagent-driven-development from spec
  docs/superpowers/specs/2026-09-03-supabase-auth-design.md, plan
  docs/superpowers/plans/2026-09-03-supabase-multi-user-auth.md,
  commits dc97427..a2ba125 on feature/authenticate, merged here at
  7520cfd). Replaces the shared-secret AUTH_TOKEN scheme with real
  Supabase email/password accounts: local (no-network) JWT
  verification, a device_links ownership table, /auth/config,
  POST/DELETE /instances/{id}/link, and login/register GUIs across web
  PWA, mobile, and the desktop tray. Final whole-branch review caught a
  Critical IDOR (instance-scoped routes weren't checking device_links
  ownership, only the list view was filtered) — fixed and re-reviewed
  clean in commit a2ba125.
- Ruling (residual, not fixed — no second fix wave permitted at that
  point): that same fix introduced an id-format mismatch on the 2
  legacy routes (POST /select, GET /windows) — they check ownership
  against "adb:SERIAL" but device_links only ever stores bare serial
  (the real InstanceManager's key format), so under auth-enabled mode
  these two routes now 403-everyone / return-empty regardless of real
  links. Fails closed (no cross-tenant access, not a security hole),
  and no client in this repo calls either legacy route — parked rather
  than blocking on a disallowed second fix wave. Fix (whenever someone
  gets to it): normalize the id in _authorize_instance_access's two
  legacy call sites (src/server/app.py), or remove the legacy routes.
- Finished: merged feature/authenticate into feature/engine (clean
  merge, no conflicts) and pushed to origin/feature/engine. Verified on
  the merged result: Python 428 passed / 2 pre-existing-unrelated
  failures (test_windows_verifier.py, ambient AUTH_TOKEN env var
  leakage) / 1 skipped; Node signaling 14/14; mobile 67/67 (needed a
  fresh `npm install --legacy-peer-deps` in this worktree — mobile/'s
  node_modules had never been installed here before).
- Next: Windows Host PC should pull feature/engine and run the usual
  gates (engine_tests.exe, verify-engine-cutover.ps1) plus a manual
  Supabase-auth pass — needs a real Supabase project configured
  (SUPABASE_URL/SUPABASE_ANON_KEY/SUPABASE_JWT_SECRET/
  SUPABASE_SERVICE_ROLE_KEY) and infra/supabase/device_links.sql
  applied manually via the Supabase SQL editor first (not run by any
  test suite — see README.md's new Supabase section). Manual gate per
  spec: register a new account via web, confirm empty device list,
  link an instance, confirm it appears; log in as a second account,
  confirm it does NOT see the first account's linked instance; repeat
  login (not register) on mobile with the same account, confirm same
  list. Also worth deciding on the parked legacy-route ruling above —
  fix now or accept as known dead-route breakage.
- Blockers: none for the merge itself; Windows-side manual E2E and a
  real Supabase project are required before this can be called fully
  verified, same shape as every other plan in this repo.

---

### 2026-09-04 01:55 — claude
- Finished: fixed a visible-console-window bug found while rerunning
  task-11's matrix on the Host PC after the installer fixes (screenshot
  showed multiple stacked terminal windows, one per spawned engine
  instance). Root cause: `src/server/engine_process.py`'s
  `EngineInstance.start()` never set `CREATE_NO_WINDOW` on its
  `subprocess.Popen` call for `engine.exe` (a console-subsystem binary),
  unlike `adb_manager.py`/`scrcpy_server.py`'s existing
  `no_window_flags()` pattern which this file predates and missed.
  Added the same pattern locally (commit 21870a9). TDD: 2 new tests
  (win32 → creationflags present; darwin → absent), 15/15
  `test_engine_process.py` pass. Full suite: 398 passed, same 2
  pre-existing unrelated `test_windows_verifier.py` failures and 2
  documented collection errors.
- Next: rebuild (`build\build_installer.bat`) and rerun task-11's
  matrix on the Host PC — this should stop the console-window spam
  during multi-instance gates (rapid switches, soak if unoverridden,
  quality ladder). Not yet confirmed on real Windows.
- Blockers: none identified; fixed by code inspection from a screenshot,
  not yet confirmed by a rerun.

### 2026-09-04 01:10 — claude
- Ruling: continued 64-bit installer fix (93c3282) did NOT resolve
  task-11's installer gate — root-caused with real Windows evidence
  (not guessed): PyInstaller 6.21.0's onedir mode moves everything
  except the top-level .exe into `_internal\`, so
  `C:\Program Files\WindowControl\assets\engine\engine.exe` never
  existed; the real file is at
  `C:\Program Files\WindowControl\_internal\assets\engine\engine.exe`
  (confirmed via `dir` on the Host PC). `src/config.py`/`main.py`
  already handle this correctly for the running app via
  `sys._MEIPASS`, but two other places assumed the old flat layout.
- Finished: fixed both (commit b5cb61d). (1)
  `scripts/verify_engine_cutover.py`'s `verify_installer()` engine
  path. (2) More importantly, `build/installer.iss`'s
  `AddEngineFirewallRule()` — the Windows Firewall allow-rule for the
  engine was pointing at a nonexistent path, so it never matched the
  real `engine.exe` process. This was a real production bug (silently
  broken firewall allow-rule), not just a verifier/test-tooling bug —
  worth calling out since it predates this session's task-11 work and
  would have shipped as-is. 71/71 focused tests pass
  (`test_engine_cutover_verifier.py` + `test_build_files.py`); full
  suite 396 passed, same 2 pre-existing unrelated `test_windows_verifier.py`
  failures (env-var pollution from this shell) and the same 2
  documented pre-existing collection errors.
- Next: rerun `build\build_installer.bat` then the installer gate on
  the Host PC. If it fails again, check
  `C:\Program Files\WindowControl\_internal\assets\engine\engine.exe`
  directly and the `netsh advfirewall firewall show rule name="WindowControl-Engine"`
  output before assuming another guess.
- Blockers: none identified; unverified on real Windows since fixed by
  code inspection + the Host PC evidence gathered this round, not by
  an actual rerun yet.

### 2026-09-04 00:20 — claude
- Finished: two more local-build fixes hit while rerunning
  2026-09-01-engine-client-cutover/task-11's installer gate on the
  Windows Host PC. (1) `build/build_installer.bat` now auto-downloads
  `vc_redist.x64.exe` if missing (commit 8fa5596) — installer.iss needs
  it but only CI's workflow ever fetched it, so every local
  `build_installer.bat` run failed at ISCC compile. (2) `installer.iss`
  now sets `ArchitecturesAllowed`/`ArchitecturesInstallIn64BitMode=x64compatible`
  (commit 93c3282) — without it Inno Setup defaults 32-bit and `{autopf}`
  resolves to `Program Files (x86)`, while the verifier checks
  `%PROGRAMFILES%` (`Program Files`); root-caused by inspection (no
  Windows access this session), not yet confirmed by an actual rerun.
  Both WindowControl.exe and the bundled engine.exe/DLLs are x64-only, so
  32-bit placement was wrong independent of the verifier mismatch.
- Next: rerun `build\build_installer.bat` then the cutover verifier's
  installer gate on the Host PC to confirm the 64-bit fix actually
  resolves "installer did not stage the executable and bundled engine".
  If it still fails, check the actual install location by hand
  (`C:\Program Files\WindowControl\` vs `C:\Program Files (x86)\WindowControl\`)
  before assuming the architecture fix was sufficient.
- Blockers: none identified; unverified on real Windows since fixed by
  code inspection only.

### 2026-09-03 23:50 — claude
- Ruling: owner explicitly authorized `OVERRIDE: skip 8-hour soak rerun;
  single-minute decode-stall accepted as known stability gap, all other
  gates PASS` for 2026-09-01-engine-client-cutover/task-11. Latest Windows
  run: performance overridden (pre-existing), local browser, public
  browser, mobile, local/public race, rapid switches, quality ladder,
  scrcpy recovery, and engine recovery all PASS; only soak FAILED —
  `frames_decoded` plateaued for one minute somewhere in the 8h run (no
  zero/black-frame samples, connection stayed alive). Owner has not yet
  checked `app.log`/`verification.log` in that run's evidence dir for the
  stall timestamp and does not have time to rerun the full 8h soak now.
- Finished: `--soak-override` added to `scripts/verify_engine_cutover.py`
  (commit a36192c) mirroring the existing `--performance-override`
  pattern exactly — requires the exact recorded string, marks the
  checkpoint OVERRIDDEN (not PASS), skips the real 8h run entirely; wired
  through `main()` CLI arg and `engine/verify-engine-cutover.ps1`'s new
  `-SoakOverride` switch (opt-in, unlike the always-on performance
  override). TDD: 60/60 `tests/test_engine_cutover_verifier.py` pass
  (2 new: override skips the real run and is recorded OVERRIDDEN; an
  unrecognized override string still fails the gate). Full local suite:
  395 passed, 2 pre-existing unrelated failures in
  `tests/test_windows_verifier.py` (env-var pollution from this shell,
  confirmed pre-existing via stash-and-rerun, not caused by this change),
  same 2 documented pre-existing collection errors
  (`test_auto_unlock.py`, `test_window_manager.py`).
- Next: on the Windows Host PC, rerun
  `.\engine\verify-engine-cutover.ps1 ... -SoakOverride` (plus the same
  args as before) to get a final result with soak OVERRIDDEN instead of
  FAIL. The underlying decode-stall bug is NOT diagnosed or fixed — it is
  an accepted, recorded risk. Root-cause investigation is blocked on: (1)
  the exact minute/sample index frames_decoded stalled in the failed
  run's `soak-samples.json`, (2) `app.log`/`verification.log` from that
  evidence dir around that timestamp, (3) whether it correlates with an
  ICE 'disconnected' transition (recent commit `7f9b712` added grace-period
  handling for that state — worth checking if detection fires but no
  reconnect follows). Revisit before shipping if the stall recurs in
  production.
- Blockers: none for completing Task 11's acceptance run with the
  override; the decode-stall root cause remains open and un-investigated.

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
