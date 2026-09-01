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
  Windows behavior); `jest` via `npm test` (`mobile/`); `engine_tests.exe`
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
- YYYY-MM-DD:
