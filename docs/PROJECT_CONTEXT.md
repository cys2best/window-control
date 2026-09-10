# Project Context

## Overview
WindowControl (EmuCtrl) streams specific Windows application windows to mobile and web clients via WebRTC and a native C++ engine. The stack uses Python/FastAPI and PyQt5 on desktop, Next.js for web, Expo/React Native for mobile, and shared TypeScript core/UI packages.

## Commands
- Verify: `uv run pytest tests/ -v && npm run test:core && npm run test:ui && npm test -w apps/web`
- Focused test: `uv run pytest tests/ -k "<name>"`
- Build: `npm run build -w apps/web`
- Setup: `uv sync && npm install`

## Boundaries
- `engine/`: Windows-only C++ WebRTC engine; macOS runs against stubs, full validation requires Windows or CI `build-engine`.
- `apps/web`: Next.js static export must be built (`npm run build -w apps/web`) before running FastAPI or packaging.
- `src/assets/`: Runtime engine and scrcpy binaries are generated or downloaded; do not manually populate or commit.
- `apps/mobile`: Screen and component tests live in `packages/core` and `packages/ui`; `apps/mobile` contains no local test files.
- `docs/*`: Mostly gitignored; track new documentation files explicitly using `git add -f`.
- Avoid modifying or scanning vendor and build directories: `node_modules/`, `.venv/`, `build/`, `dist/`, `apps/web/out/`.

## Conventions
<!-- agent-sync:project-policy:start -->
- Commit format: <type>(optional-scope): imperative description
- Commit source: git history
- Commit example: `feat(brand): ship the locked EmuCtrl mark across platforms`
- Live execution state (task briefs, reports, progress) is owned by Superpowers at `.superpowers/sdd/`, `docs/superpowers/` — don't hand-edit these or create files there yourself; that's the tool's job.
- Think Before Coding: State consequential assumptions and tradeoffs; ask when ambiguity changes the result, and suggest a simpler approach when appropriate.
- Simplicity First: Implement only the requested behavior with the smallest clear solution; avoid speculative features, configuration, and abstractions.
- Surgical Changes: Match local style, change only what the task requires, and remove only code made unused by your changes; flag unrelated cleanup separately.
- Learning: Keep up to five one-line lessons (25 words each) as `Component: pitfall → action`; merge duplicates, replace obsolete entries, and prefer regression tests.
- Context upkeep: Aim for 80 lines, at most 120 lines and 1,200 words; keep actionable facts once, link to existing detail, and omit history, progress, and empty sections.
<!-- agent-sync:project-policy:end -->
- Frontend versioning: Bump `VERSION` in `src/config.py` when updating frontend code to maintain release hygiene.
- Python runtime: Always execute tools via `uv run` (`uv run python`, `uv run pytest`), never raw system binaries.

## Lessons
- Auth Gate: Replacing ACL tables can introduce authorization leaks → Verify replacement handlers check ownership lock (e.g. claim-once-then-lock in `_auth_gate`).
- Signaling Tests: WebSocket client open fires before server accepts connection → Pair connection with `waitForCloseCode` to ensure server accepted token.
- Next.js Export: Client router fetches `<route>.txt` RSC payloads on navigation → Serve `.txt` payloads alongside HTML to prevent hard-navigation 404s.
- Test Coverage: Unwired test suites cause silent regressions → Ensure all new package tests are wired into root npm scripts and CI.
