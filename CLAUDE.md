# Claude Code Instructions

<!-- agent-sync:agent-policy:start -->
Read and follow [AGENTS.md](AGENTS.md) before doing anything in this repository.
<!-- agent-sync:agent-policy:end -->

After each fix, if dont tell anything then you can push code, but when creating git commits, do not add Co-Authored-By lines.

- After editing frontend JS/CSS, bump `VERSION` in `src/config.py`. Historically `app.py` used this to append `?v={VERSION}` to asset URLs and bust the browser cache; since the 2026-09-05 unified-frontend cutover, `apps/web`'s Next.js export content-hashes its own bundle filenames instead (a real content change always changes the URL), so that specific rewrite was removed as redundant — the bump-on-change habit still stands as the project's general cache-hygiene convention.
- Python commands: use `uv run pytest` / `uv run python`, never bare `python`/`pytest` (uv-managed project).

## Repo layout
- `src/` — main Python/FastAPI Windows app (primary, most active). Run: `uv sync && uv run python src/main.py`. Test: `uv run pytest tests/ -v` (runs on Mac via `src/stubs/` — Win32/mss stubbed, so a pass here doesn't confirm Windows behavior). Serves `apps/web`'s static export as the UI (`WEB_BUILD_DIR` in `src/config.py`) — it no longer has its own client code.
- `engine/` — new C++ WebRTC engine, Windows-only, has **never been successfully compiled** (see `engine/BUILD_WINDOWS.md`) — treat it as unverified, don't assert it builds or works without checking the `build-engine` GH Actions workflow.
- `apps/web` — Next.js app (`output: "export"`), the browser/PWA client. Build: `npm run build -w apps/web` (produces `apps/web/out`, which `src/server/app.py` serves and `build/window_control.spec` bundles into the installer — build it before running the FastAPI app or packaging). Test: `npm test -w apps/web`.
- `apps/mobile` — Expo/React Native iPhone client (relocated from the old top-level `mobile/`). `npm start`/`npm test` inside `apps/mobile/`.
- `apps/desktop` — pywebview desktop shell (`tray.py`, `window.py`, `webview_main.py`; `tray.py` relocated from `src/gui/tray.py`) embedding `apps/web`'s build in a native window alongside the existing PyQt5 tray/launcher. The webview runs in a **child process** (`webview_main.py`, spawned by `window.py`'s `DesktopWindow.show()`) — `webview.start()` only runs on a process main thread, and PyQt5 owns the main one; never move it back onto a thread. Test: `uv run pytest apps/desktop/ -v` (part of `src`'s Python toolchain, not a separate npm workspace).
- `packages/core`, `packages/ui` — shared TypeScript logic and React (`react-native-web`) UI consumed by both `apps/web` and `apps/mobile`. Test: `npm run test:core` / `npm run test:ui`.
- `infra/` — Terraform for the VPS (coturn TURN server + signaling bridge).
