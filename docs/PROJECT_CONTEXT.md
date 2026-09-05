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
  on the Windows Host PC on 2026-08-31 — see `HANDOFF.md`); TypeScript / React
  Native / react-native-web (`packages/core`, `packages/ui`, shared between
  `apps/web` and `apps/mobile`); Next.js (`apps/web`, static export, the
  browser/PWA client); Expo / React Native (`apps/mobile`); Python /
  `pywebview` (`apps/desktop`, wraps `apps/web`'s build in a native window
  alongside the existing PyQt5 tray/launcher); Terraform (`infra/`, VPS:
  coturn TURN server + signaling bridge).
- Package manager: `uv` (Python, `src/` + `apps/desktop`); npm workspaces at
  the repo root (`apps/web`, `apps/mobile`, `packages/core`, `packages/ui`);
  vcpkg + CMake (`engine/`, Windows CI only).
- Test runner: `pytest` via `uv run pytest tests/ -v` (`src/`, runs on Mac
  against `src/stubs/` — Win32/mss stubbed, so a pass doesn't confirm
  Windows behavior) and `uv run pytest apps/desktop/ -v` (same Python
  toolchain, not a separate npm workspace); `node:test` via `npm test`
  (`infra/vps/signaling/`); `jest` via `npm run test:core` / `npm run
  test:ui` (root-level, `packages/core`/`packages/ui`), and `npm test -w
  apps/web`. `apps/mobile` has intentionally had zero local test files
  since Task 8 of the 2026-09-05 unified-frontend cutover moved every
  screen/component test into `packages/ui` alongside the code it tests —
  `cd apps/mobile && npx jest` exiting "No tests found" is the expected,
  correct result, not a regression to chase; its coverage lives in
  `packages/core`/`packages/ui` instead. `engine_tests.exe`
  (gtest, Windows-only, runs in CI's `build-engine` job, excludes
  `SignalingClient.*` — no server available there).
- Lint / format command: not detected — fill in manually.

## Build & verify commands
```
# install
uv sync                      # src/, apps/desktop
npm install                  # repo root (npm workspaces: apps/web, apps/mobile, packages/core, packages/ui)

# build
npm run build -w apps/web    # apps/web/out (Next.js static export) -- build this BEFORE running src/main.py or packaging; src/server/app.py serves it, build/window_control.spec bundles it
uv run python src/main.py    # run the app directly
cmake -S engine -B engine/build ...  # engine/, Windows-only, see engine/BUILD_WINDOWS.md
python scripts/bump_version.py       # sync VERSION across config.py / pyproject.toml / installer.iss

# test
uv run pytest tests/ -v      # src/ (Mac-stubbed, doesn't confirm Windows behavior)
uv run pytest apps/desktop/ -v  # apps/desktop (tray.py, window.py)
npm run test:core            # packages/core (jest)
npm run test:ui              # packages/ui (jest)
npm test -w apps/web         # apps/web (jest)
npm test                     # infra/vps/signaling/ (node:test, relay contract)
# apps/mobile has no local test files (all moved into packages/core/ui in
# Task 8 of the 2026-09-05 cutover) -- `cd apps/mobile && npx jest`
# correctly exits "No tests found"; that is not a regression.

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
  - Never call `webview.start()` (pywebview) from a background thread in
    `src/main.py`'s process — it raises `WebViewException('pywebview must
    be run on a main thread.')` before doing anything else, and PyQt5's
    `QApplication.exec_()` holds this process's main thread for the app's
    whole life, so there is no thread to give it. The desktop shell
    therefore runs in its own child process
    (`apps/desktop/webview_main.py`). This shipped broken once: the
    background-thread version raised inside a daemon thread on every
    "Open App" click, so no window ever opened and no error ever
    surfaced. A unit test that patches `window.webview` wholesale cannot
    catch this class of bug — the guard is a thread-identity check inside
    the real library, so the test has to exercise a real `start()` call
    (see `apps/desktop/test_window.py`).
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
  - After editing frontend code, bump `VERSION` in `src/config.py`. Before
    the 2026-09-05 unified-frontend cutover this mattered because
    `app.py` appended `?v={VERSION}` to the old hand-rolled client's
    fixed-name asset URLs to bust browser cache; `apps/web`'s Next.js
    export content-hashes its own bundle filenames instead, so that
    rewrite is gone from `app.py` (confirmed redundant by inspecting a
    real build's output before removing it) — the bump-on-change habit
    is now just general hygiene, not load-bearing for cache-busting.
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
  - Historical lesson (the underlying files no longer exist, but the
    principle still applies elsewhere): `tests/client/*.test.js` tested
    the old hand-rolled `src/client/*.js` and was never wired into any
    npm script or CI — only documented as a manual command in this file.
    A task that changed `src/client/*.js` without explicitly running that
    command could silently regress it undetected (happened once: a field
    rename dropped `tests/client/engine_session.test.js` from 25/25 to
    14/25, unnoticed until a final whole-branch review measured it
    directly). The 2026-09-05 unified-frontend cutover deleted both
    `src/client/` and `tests/client/*.test.js` together (their target
    code and its only test suite, dead as a pair) — the equivalent logic
    now lives in `packages/core`/`packages/ui`/`apps/web`, each with a
    real jest suite wired into `.github/workflows/frontend-packages.yml`
    from the commit that added it, specifically to not repeat this. If a
    new test suite is ever added without wiring it into CI, treat that as
    the same risk recurring.

## Plan & spec structure
- Multiple plans can be active at once. See HANDOFF.md for which agent
  owns which plan/task right now.

## Architecture notes
- Monorepo. Python side:
  - `src/` — main Python/FastAPI Windows app (primary, most active).
    Serves `apps/web`'s static export as its UI; has no client code of
    its own.
  - `apps/desktop` — pywebview desktop shell (`tray.py`, `window.py`),
    part of the same Python/`uv` toolchain as `src/` (not an npm
    workspace), embedding `apps/web`'s build in a native window.
  - `engine/` — new C++ WebRTC engine intended to replace parts of `src/`,
    Windows-only, unbuilt/unverified.
- npm-workspaces side (root `package.json`), replacing the old
  `src/client`/top-level `mobile/` split (2026-09-05 unified-frontend
  cutover — see Decisions log):
  - `packages/core` — shared TypeScript logic (API client, WHEP/engine
    session state machine, quality tiers, input-channel protocol,
    Supabase auth) consumed by both `apps/web` and `apps/mobile`.
  - `packages/ui` — shared React components/screens (`react-native-web`)
    built on `packages/core`, consumed the same way.
  - `apps/web` — Next.js app (`output: "export"`), the browser/PWA
    client `src/server/app.py` serves.
  - `apps/mobile` — Expo/React Native iPhone client (relocated from the
    old top-level `mobile/`), refactored onto `packages/core`/`packages/ui`.
- `infra/` — Terraform for the VPS (coturn TURN server + signaling
  bridge) supporting the public WebRTC path.
- CI: `.github/workflows/build.yml` builds `apps/web`'s static export and
  the C++ engine on `windows-latest`, then builds the installer;
  triggered on `v*` tags. `.github/workflows/frontend-packages.yml` runs
  `packages/core`/`packages/ui`/`apps/web`'s jest suites on every push/PR
  (not tag-gated).

## Decisions log
<!-- Promote real decisions here as they're made. Newest on top. -->
- 2026-09-05: unified the three duplicated frontends (`src/client` vanilla
  JS, `mobile/` Expo/React Native, and desktop's "open a system browser"
  gap) into one monorepo: `packages/core` (shared logic) + `packages/ui`
  (shared `react-native-web` components), consumed by `apps/web` (new
  Next.js static export, replaces `src/client`), `apps/mobile` (relocated
  `mobile/`, refactored onto the shared packages), and `apps/desktop`
  (new `pywebview` shell embedding `apps/web`'s build, alongside the
  existing PyQt5 tray/launcher rather than replacing it — the launcher's
  QR-pairing/update-banner panel stayed, gaining an "Open App" button
  rather than being removed, since it does something `apps/web` doesn't).
  The shell runs in a **child process** (`apps/desktop/webview_main.py`,
  spawned by `DesktopWindow.show()`; the frozen app re-invokes itself
  with `--webview-window <url>`), because `webview.start()` refuses to
  run anywhere but a process's main thread and PyQt5's
  `QApplication.exec_()` owns this process's for the app's whole life —
  see "Things NOT to do".
  Single cutover: `src/client/` and `mobile/`'s local duplicated modules
  were deleted in the same task that finished wiring `apps/desktop`
  (Task 10), not staged incrementally. Real gaps found and fixed while
  wiring FastAPI to the new build (none were called out by the plan
  going in): apps/web's page routes for `/login`/`/stream` collide by
  path with a pre-existing legacy `POST /login` route and a removed
  legacy Android-MJPEG `/stream` route respectively (resolved: the new
  GET page routes and old routes don't actually conflict in practice —
  POST /login correctly 405s instead of 404ing, and /stream now serves
  the page shell, not MJPEG); `/instances` collides with the existing
  JSON API route of the same name. That collision was first written up
  as a narrow, unreachable limitation ("in-app client-side navigation is
  unaffected") — **that was wrong**, and the task's own review caught it
  against the shipped bundle: Next 15's client router does not soft-
  navigate on its own, it first fetches the destination's prerendered RSC
  payload at `<route>.txt`, and falls back to a full `window.location`
  page load whenever that fetch 404s. FastAPI served no `.txt` files, so
  `router.replace("/instances")` — the app's default destination after
  every login — hard-navigated onto the JSON API route and rendered raw
  JSON. Fixed in the same plan's fix round: `app.py` serves the export's
  `.txt` payloads (as `text/x-component`, one of the two content types
  the router accepts) plus `manifest.json`/`icon-192.png`/`404.html`, and
  `GET /instances` content-negotiates — `Accept: text/html` gets the page
  shell, everything else (`packages/core`/`apps/mobile` send a plain
  `fetch()` with no `Accept`) gets the unchanged JSON list. The auth gate
  and that handler branch on one shared predicate deliberately, so an
  unauthenticated HTML-shaped request can only ever reach the static
  shell, never the list. General lesson: a static export's route table
  is not just its `.html` files — check what the framework's own client
  router fetches at runtime before declaring a routing gap unreachable.
  See
  `docs/superpowers/specs/2026-09-05-react-unified-frontend-design.md`
  and this plan's Task 10 report for the full account.
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
