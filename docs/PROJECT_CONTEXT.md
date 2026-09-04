# Project Context

> Single source of truth for project knowledge. Per-agent context files
> import or point to this file — edit it here, not in any of those.

## What this project is
WindowControl streams specific Windows 11 application windows to an iPhone
over Tailscale (or LAN), with touch input, virtual keyboard relay, and a
PWA client. Distributed as a Windows installer built via PyInstaller.

## Tech stack
- Language / framework: Python 3.11+ / FastAPI (primary app, `src/`); C++ /
  WebRTC (new engine, `engine/`, Windows-only, successfully built and verified
  on the Windows Host PC on 2026-08-31 — see `HANDOFF.md`); Expo / React Native
  (`mobile/`); Terraform (`infra/`, VPS: coturn TURN server + signaling
  bridge).
- Package manager: `uv` (Python, `src/`); npm (`mobile/`); vcpkg + CMake
  (`engine/`, Windows CI only).
- Test runner: `pytest` via `uv run pytest tests/ -v` (`src/`, runs on Mac
  against `src/stubs/` — Win32/mss stubbed, so a pass doesn't confirm
  Windows behavior); `node --test tests/client/engine_session.test.js`
  (browser client modules, repo root); `node:test` via `npm test`
  (`infra/vps/signaling/`); `jest` via `npm test` (`mobile/`); `engine_tests.exe`
  (gtest, Windows-only, runs in CI's `build-engine` job, excludes
  `SignalingClient.*` — no server available there).
- Lint / format command: not detected — fill in manually.

## Build & verify commands
```
# install
uv sync                      # src/
npm install                  # mobile/ (run from inside mobile/)

# build
uv run python src/main.py    # run the app directly
cmake -S engine -B engine/build ...  # engine/, Windows-only, see engine/BUILD_WINDOWS.md
python scripts/bump_version.py       # sync VERSION across config.py / pyproject.toml / installer.iss

# test
uv run pytest tests/ -v      # src/ (Mac-stubbed, doesn't confirm Windows behavior)
npm test                     # mobile/ (jest)
npm test                     # infra/vps/signaling/ (node:test, relay contract)
node --test tests/client/engine_session.test.js   # browser client (node:test)
# tests/client/browser_cutover.test.js is known-failing (pre-existing rot)

# lint
# not detected — fill in manually
```

## Conventions
- Branch naming: `feature/<slug>` (e.g. `feature/aiortc`, `feature/mobile-application`) off `main`
<!-- agent-sync:project-policy:start -->
- Commit message format: `<type>(optional-scope): imperative description`
- Commit convention source: git history
- Commit examples: `fix(webrtc_manager): reap disconnected PCs and request IDR only once connected`; `feat(scrcpy_session): add aiortc on-demand video path alongside ffmpeg`
- Live execution state (task briefs, reports, progress) is owned by
  Superpowers at `.superpowers/sdd/`, `docs/superpowers/` — don't
  hand-edit these or create files there yourself; that's the tool's
  job.
<!-- agent-sync:project-policy:end -->
- Code style notes: not detected — fill in manually (no ruff/black/eslint config found)
- Things NOT to do (generated files to leave alone, dirs to avoid, etc.):
  - Don't assert later `engine/` C++ changes build or work from macOS-only
    checks. The 2026-08-31 Windows Host PC baseline passed 81 offline tests,
    the live-signaling suite, and the real-device manual E2E gate; subsequent
    changes still require Windows or `build-engine` verification.
  - `src/assets/scrcpy/` is downloaded at startup by `download_assets.py` —
    not tracked, don't hand-populate. `src/assets/engine/` (engine.exe +
    runtime DLLs) is staged locally by `build/build.bat` from the Release
    CMake build, or by CI's `build-engine` job artifact — not downloaded by
    `download_assets.py`. `mediamtx` is no longer part of the product; a
    stale `src/assets/mediamtx/` from an old checkout is removed by
    `build/build.bat` before packaging.
  - After editing frontend JS/CSS, bump `VERSION` in `src/config.py` —
    `app.py` appends `?v={VERSION}` to asset URLs to bust browser cache.
  - `docs/*` is gitignored except `docs/TROUBLESHOOTING.md` and files
    force-added before the ignore rule (e.g. `docs/superpowers/`) — a
    newly created `docs/` file needs `git add -f` to be tracked.
  - When removing or replacing an access-control mechanism (an ACL table,
    a per-instance link, a shared secret), explicitly verify what the
    *replacement* code path grants — don't assume "authenticated" implies
    "authorized." The 2026-09-04-public-session-isolation plan removed
    `device_links` (per-instance ACLs, correctly judged unnecessary) but
    the login-time code that took its place silently let *any*
    authenticated account adopt/overwrite an already-claimed install's
    ownership — caught only by a final whole-branch review, not any
    single task's own review, because no individual task's diff showed
    the missing check. Fixed by claim-once-then-lock (see `_auth_gate` in
    `src/server/app.py`). When a task removes a check, the very next
    thing to ask is "what enforces this now, and did anyone actually
    verify that?"
  - In `infra/vps/signaling/server.test.js` (Node `node:test` + `ws`),
    the pattern `await openClientWithToken(...); assert.ok(true);` (no
    `waitForCloseCode` check) does **not** prove the server accepted the
    connection — the `ws` library fires the client's `'open'` event once
    the WebSocket handshake completes, which happens *before* the
    server's async `connection` handler runs its own accept/reject logic.
    A test using only this pattern passes even if the server always
    rejects moments later. Proven by direct sabotage experiment during
    the same plan's final review. Always pair `openClientWithToken` with
    `const closeCode = await waitForCloseCode(ws, 200); assert.strictEqual(closeCode, null);`
    when a test's whole point is proving acceptance.
  - `tests/client/*.test.js` (browser client modules, repo root) is not
    wired into any npm script or CI — it's only run via the exact command
    documented in this file's Build & verify commands section. A task
    that changes `src/client/*.js` without explicitly running that
    command can silently regress it undetected (happened once already:
    a field rename dropped `tests/client/engine_session.test.js` from
    25/25 to 14/25, unnoticed until a final whole-branch review measured
    it directly). Always run it after touching `src/client/`.

## Plan & spec structure
- Multiple plans can be active at once. See HANDOFF.md for which agent
  owns which plan/task right now.

## Architecture notes
- Monorepo with four components:
  - `src/` — main Python/FastAPI Windows app (primary, most active).
  - `engine/` — new C++ WebRTC engine intended to replace parts of `src/`,
    Windows-only, unbuilt/unverified.
  - `mobile/` — Expo/React Native iPhone client.
  - `infra/` — Terraform for the VPS (coturn TURN server + signaling
    bridge) supporting the public WebRTC path.
- CI (`.github/workflows/build.yml`) builds the C++ engine on
  `windows-latest`, then builds the installer; triggered on `v*` tags.

## Decisions log
<!-- Promote real decisions here as they're made. Newest on top. -->
- 2026-09-04: public-relay signaling no longer trusts a secret shared
  across every install (forgeable, and let two installs collide on the
  same session name) — replaced with account-verified access: viewers
  present their own Supabase login, engines sign with a per-install
  Ed25519 keypair registered to the owning account (`installs` table).
  Sessions are now `{owner_user_id}.{instance_name}`. `device_links`
  (per-instance linking) removed in the same plan — this app is
  one-owner-per-install, so per-instance ACLs inside one PC's own list
  didn't apply. An install now claims to whichever account authenticates
  first (trust-on-first-use) and then locks — switching owners requires
  local filesystem access (delete `install_owner.txt`), not just a new
  login. See docs/superpowers/specs/2026-09-04-public-session-isolation-design.md
  and HANDOFF.md's 2026-09-04 21:45 entry for the two real regressions a
  final whole-branch review caught (an ownership-hijack hole, and an
  untested client-suite regression) and how they were fixed.
- YYYY-MM-DD:
