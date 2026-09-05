# Windows Manual Validation Runbook — v3.0.0 (feature/engine → main)

Purpose: close the testing debt recorded in
`release-reports/release-feature-engine-2026-09-05.md`. Every item below is
something that has **never been run on real Windows hardware** — it was
either overridden by owner decision, or is architecturally impossible to
verify from the macOS sessions that built it (no engine.exe, no pywebview
window, no PyInstaller build).

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

Run in order. Each step has: what to do, what "pass" looks like, and where
to log the result. Stop and report back (don't improvise a fix) if a step
fails — file:line context for the relevant code is in
`HANDOFF.md` and the plan docs under `docs/superpowers/plans/`.

**Log your results** by editing `test-cases/master.json`'s matching entries
(flip `"status"` / add a `"last_verified"` note) or, at minimum, appending a
dated entry to `HANDOFF.md` in this repo's usual format — this is the only
way the next macOS-only session will know these gates actually ran.

---

## 0. One-time machine setup

- [ ] Windows 11, real hardware or a VM with a real display (pywebview needs
      WebView2 — check `Get-AppxPackage -Name "*WebView2*"` or install the
      [Evergreen Runtime](https://developer.microsoft.com/microsoft-edge/webview2/)
      if missing).
- [ ] **Visual Studio 2022** with "Desktop development with C++", **vcpkg**
      cloned+bootstrapped, **CMake >= 3.24** — see `engine/BUILD_WINDOWS.md`
      for exact steps if you haven't set this up before.
- [ ] **Python 3.11+** and [`uv`](https://docs.astral.sh/uv/) installed.
- [ ] **Node.js** (matching `.github/workflows/*.yml`'s version) + npm.
- [ ] **ADB** on PATH, at least one real Android device or emulator
      (5 needed only for the performance step, which is out of scope this
      round — 1 is enough for everything else here).
- [ ] `git checkout feature/engine && git pull`.
- [ ] A real Supabase project (see `README.md`'s Supabase section) with
      `infra/supabase/installs.sql` applied via the SQL editor. Set
      `SUPABASE_URL` / `SUPABASE_ANON_KEY` / `SUPABASE_SERVICE_ROLE_KEY`
      env vars. **Two** test accounts (emails you can log into) — you need
      a second account for the isolation checks in section 3.
- [ ] `TAILSCALE` installed and signed in if you're testing the public/LAN
      split realistically; otherwise LAN-only is fine for most of this.

---

## 1. Build everything

```powershell
cd <repo-root>
uv sync
npm install
```

```powershell
# Engine (Release)
cmake -S engine -B engine\build `
  -DCMAKE_TOOLCHAIN_FILE=<path-to-vcpkg>\scripts\buildsystems\vcpkg.cmake `
  -DVCPKG_TARGET_TRIPLET=x64-windows
cmake --build engine\build --config Release
```
- [ ] **Pass condition:** `engine\build\Release\engine.exe` exists, build
      has zero errors. This alone closes "engine/ has never compiled" —
      if it fails, this is a Critical finding, stop here and report the
      exact compiler error (check `engine/BUILD_WINDOWS.md`'s "Known
      friction points" first, most first-build failures are already
      documented there).

```powershell
cmake --build engine\build --config Release --target engine_tests
.\engine\build\Release\engine_tests.exe --gtest_filter=-SignalingClient.*
```
- [ ] **Pass condition:** full offline suite green (baseline: 81 tests per
      the last recorded Windows run in HANDOFF.md — a different count on a
      fresh build isn't automatically wrong, but investigate any drop).

```powershell
npm run build -w apps/web
```
- [ ] **Pass condition:** `apps/web/out/` contains `index.html`, `login.html`,
      `setup.html`, `instances.html`, `stream.html`, `404.html`,
      `manifest.json`, `icon-192.png`, and a `.txt` file alongside each
      `.html` (the RSC payloads Task 10's fix added — their absence means
      the build is stale or something regressed).

---

## 2. Offline automated suites (quick sanity before manual work)

```powershell
uv run pytest tests/ -v
uv run pytest apps/desktop/ -v
```
- [ ] **Pass condition:** matches the last documented macOS baseline (456
      passed / 2 pre-existing `test_windows_verifier.py` failures / 1
      skipped / 2 pre-existing collection errors for `test_auto_unlock.py`
      and `test_window_manager.py`; `apps/desktop` 15/15). **A Windows run
      might make the 2 `test_windows_verifier.py` failures disappear**
      (they were macOS env-var-pollution artifacts) — if they now pass,
      that's expected and good, note it.

```powershell
npm run test:core; npm run test:ui; npm test -w apps/web
```
- [ ] **Pass condition:** 45/45, 21/21, 4/4 respectively.

```powershell
.\engine\build\Release\engine_tests.exe --gtest_filter=SignalingClient.*
```
Needs a running relay — either the repo's own loopback Python relay
(`engine/test/README_e2e.md` documents this) or `infra/vps/signaling`'s
Node relay (`cd infra/vps/signaling && npm install && node server.js`, or
whatever its actual start command is — check its `package.json`).
- [ ] **Pass condition:** SignalingClient/PublicSignalingBridge suite green
      against a real relay. This is normally excluded from CI/offline runs
      — this may be the first time it's run against *this* commit range.

---

## 3. Supabase multi-user auth manual gate

This has a documented history of real bugs only found by a human clicking
through it (see HANDOFF.md's 2026-09-04 entries) — go carefully, don't
skim.

- [ ] Start the app: `uv run python src\main.py` (dev mode — confirms the
      engine orchestrator can actually find `engine.exe` now, which it
      could never do on macOS).
- [ ] Open the app's web UI in a browser (`http://<PC-IP>:8080` or via
      Tailscale). Register a brand-new account (Account A).
- [ ] **Pass condition:** empty instance list on first login (no
      auto-claimed devices from a stale `install_owner.txt` — delete that
      file first if this is a reused machine).
- [ ] Confirm the device gets claimed on its first authenticated request
      (trust-on-first-use). Log out, log in again as Account A — same
      instance still shows.
- [ ] Log in as a **second, different account (Account B)**.
- [ ] **Pass condition (Critical):** Account B does **not** see Account A's
      claimed instance, and any attempt to act on it returns `403`, not a
      silent adoption. This is the exact hole a prior whole-branch review
      found and fixed (`_auth_gate` in `src/server/app.py`) — re-confirm it
      holds on real hardware, not just in the test suite.
- [ ] Repeat login (not register) on the mobile app (`apps/mobile`, run via
      Expo Go or a dev build) with Account A — confirm it shows the same
      linked instance list as web.
- [ ] Repeat on the desktop tray's launcher (see section 5) if it exposes
      login — confirm parity.

---

## 4. Core streaming path (engine, local + public)

- [ ] With the app running and Account A's device connected, open the web
      stream page for the instance. **Pass condition:** live, non-black
      video, climbing `framesDecoded` (check via the stream's stats
      overlay or `/admin/health` if exposed), working touch/click input,
      working virtual keyboard.
- [ ] Repeat over Tailscale/public relay (not just LAN) if you have the VPS
      configured — confirm the public path also streams.
- [ ] Rapid device switching: pick 2+ ready devices, switch between them
      ~20 times in the UI. **Pass condition:** no crash, no stuck black
      frame, no orphaned engine processes left in Task Manager after the
      loop.
- [ ] Quality ladder: change quality tier mid-stream a few times.
      **Pass condition:** stream adapts without a full reconnect/black
      frame.
- [ ] Kill the scrcpy/engine process for a connected device out from under
      the app (Task Manager). **Pass condition:** app detects and recovers
      (respawns) without requiring an app restart.

---

## 5. Desktop shell (apps/desktop) — the highest-priority never-tested item

This is the component the last two code reviews flagged as the biggest
unverified risk in the whole plan — the fix (subprocess-based pywebview) is
architecturally sound on paper but has literally never run on a machine with
a real display.

- [ ] From the tray icon, click "Show" → confirm the existing PyQt5
      `LauncherWindow` opens (QR code, server IP, update banner — this part
      is unchanged, should just work).
- [ ] Click the **"Open App"** button.
- [ ] **Pass condition (Critical):** a real native window opens (WebView2),
      loads `http://127.0.0.1:8080`, and shows the app's login/instance UI
      — not a crash, not a silently-failed subprocess spawn, not a blank
      window.
- [ ] Click "Open App" a **second time** while the first window is still
      open. **Pass condition:** does not open a second window (idempotency
      — check `DesktopWindow.show()`'s intended one-shot behavior actually
      holds when driven by a real click, not just the unit test's mock).
- [ ] Close the pywebview window, click "Open App" again. **Pass
      condition:** opens a fresh window correctly (confirms it's not
      permanently "used up" after the first close).
- [ ] Log in through the pywebview window, navigate to the instance list,
      start a stream, confirm it works exactly like it did in a normal
      browser tab (section 4). **Pass condition:** parity — no
      webview-specific quirks (Ctrl+scroll zoom breaking touch input,
      missing keyboard focus, etc.)
- [ ] **Abrupt-kill check** (a known, documented, accepted limitation — confirm
      it behaves as expected, don't expect it to be fixed): kill the main
      `WindowControl` process via Task Manager (not tray Exit) while the
      pywebview window is open. **Expected:** the webview_main.py child
      process is orphaned with its window still open, now pointed at a dead
      server (it'll show a connection-refused page or similar). This is
      documented, accepted behavior — just confirm it matches the
      documented expectation rather than crashing something worse.
- [ ] Exit cleanly via tray "Exit" with the pywebview window open. **Pass
      condition:** both the main process and the webview subprocess
      terminate — no orphaned `WindowControl.exe`/webview process left in
      Task Manager.

---

## 6. Frozen-build self-relaunch path (only testable from a packaged build)

This is a code path that **cannot be exercised** from `uv run python
src\main.py` — it only fires when the frozen exe re-invokes itself with
`--webview-window <url>`. Do this after building the installer in section 7.

- [ ] From an installed (not dev-mode) `WindowControl.exe`, click "Open
      App" and confirm the same behavior as section 5 — but this time watch
      Task Manager for a **second** `WindowControl.exe` process (the
      self-relaunched child) rather than a `python.exe`/`webview_main.py`
      process.
- [ ] **Pass condition:** the child process does not re-run the full
      app (no second tray icon, no second server trying to bind port 8080)
      — confirm `src/main.py`'s `--webview-window` dispatch really does
      short-circuit before `QApplication(sys.argv)` / server startup, as
      the code review found. If you see two tray icons or a port-8080
      bind error, that's a Critical regression.

---

## 7. Full installer build + install + uninstall

```powershell
cd build
.\build.bat
```
- [ ] **Pass condition:** completes with `dist\WindowControl\` populated,
      including `_internal\web\` (the staged `apps/web/out`) and
      `_internal\assets\engine\engine.exe`.

```powershell
# Build the Inno Setup installer (check build/installer.iss's exact ISCC invocation,
# or CI's build.yml for the command it uses)
```
- [ ] **Pass condition:** installer built successfully, includes
      `vc_redist.x64.exe` bootstrap.
- [ ] Run the installer on a clean-ish machine/VM (or at least a machine
      without a prior install). **Pass condition:** installs to
      `C:\Program Files\WindowControl\` (64-bit path, not `Program Files
      (x86)` — a past bug), Start Menu shortcut created, no errors.
- [ ] **Pass condition:** `netsh advfirewall firewall show rule
      name="WindowControl-Engine"` shows a rule pointing at the *actual*
      installed `engine.exe` path (under `_internal\assets\engine\`, not
      the old flat-layout path from a historical bug).
- [ ] Launch the installed app, repeat sections 3-6 against this real
      installed build (not the dev checkout) — this is the first time this
      exact packaging has ever been exercised end-to-end.
- [ ] Uninstall via Control Panel / Settings. **Pass condition:** clean
      removal — no leftover firewall rule, no leftover
      `C:\Program Files\WindowControl\` directory, no orphaned processes.

---

## 8. Leaked install-key cross-account forgery check

From `2026-09-04-public-session-isolation`'s spec — verifies the per-install
Ed25519 keypair can't be used to impersonate a different account's install.

- [ ] Locate the install's private key file (check `src/server/app.py` /
      `install_identity.py` for its exact path — likely near
      `install_owner.txt`).
- [ ] Copy that key file to a **second** Windows machine (or a second clean
      install directory simulating one) that has never been claimed by any
      account.
- [ ] On the second machine, run the app and let it register/sign in with
      the relay using the copied key, but log in through that install as
      **Account B** (different from whichever account owns the original
      machine).
- [ ] **Pass condition (Critical, security-relevant):** the copied key can
      only ever present as the *original* machine's already-claimed
      session — it must not let Account B silently take over or spoof a
      session that belongs to Account A's original install. If Account B
      gets access to Account A's stream, that's a real vulnerability, not
      a test failure — stop and report immediately, don't attempt a fix
      yourself without discussing scope.

---

## 9. Known accepted gaps — confirm they're still just gaps, not worse

- [ ] **8-hour soak** — full run optional this round (owner previously
      accepted `--soak-override` for a known decode-stall bug). If you have
      time: run it, and if the decode-stall reproduces, capture
      `app.log`/`verification.log` around the stall timestamp and the exact
      sample index in `soak-samples.json` — this is the evidence needed to
      finally root-cause it (never gathered before).
- [ ] **5-instance performance workload** — optional, previously overridden
      entirely. Skip unless you have 5 devices/emulators and time.
- [ ] **`scripts/verify_engine_cutover.py`** — do not expect this to work
      end-to-end; it still authenticates via the legacy shared `AUTH_TOKEN`
      scheme, incompatible with Supabase JWT auth. Confirm it still fails
      the way HANDOFF.md describes (auth-related failure, not something
      new) rather than assuming it's usable as a one-shot verifier for this
      release.

---

## 10. Reporting results

When done, update:
- `test-cases/master.json` — for each testing-debt entry (`"type": "edge"`,
  described as unverified), flip its status/add a note reflecting what you
  actually observed (pass, fail, or "still can't verify, here's why").
- `HANDOFF.md` — append a dated entry naming which sections above passed,
  which failed, and exact repro steps/evidence for any failure, following
  this repo's existing entry format (see the template near the top of the
  file).

Do **not** silently mark something "verified" in `docs/PROJECT_CONTEXT.md`'s
Decisions log or anywhere else without the evidence to back it — this repo
has been burned twice by claims like that outliving the actual gap
(soak-override's decode-stall, and Task 10's "safe" claims that a code
review later disproved). Write what you actually saw.
