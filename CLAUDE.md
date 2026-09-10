# Claude Code Instructions

<!-- agent-sync:agent-policy:start -->
This file is intentionally thin. All real project knowledge lives in the
shared files below so other agents see the same thing.

@docs/PROJECT_CONTEXT.md
@HANDOFF.md

## Claude Code specific
- Only engage Superpowers when the user's prompt explicitly names it
  or its plan/task artifacts (e.g. mentions Superpowers by name, or
  references a path under `.superpowers/sdd/`, `docs/superpowers/`). Do not infer that a task belongs to
  this workflow from task shape, complexity, or ambient activation signals
  (`.superpowers/sdd/*/progress.md`, `docs/superpowers/plans/*.md`) alone — plain requests get a direct, ordinary
  execution path. When the prompt does invoke Superpowers, follow
  these rules in order:
  1. When a requested task belongs to an active Superpowers plan, resume it through the applicable Superpowers execution workflow.
  2. Keep task briefs, reports, progress, reviews, and completion state inside the Superpowers SDD flow.
  3. Never execute a managed task manually or create or edit Superpowers-owned artifacts directly.
  4. If the required workflow cannot be invoked, stop and report the blocker.
  Do not substitute a manual or generic execution path once engaged.
- For superpowers, when running as claude, use planning=sonnet,
  implementation=haiku, review=sonnet,
  escalation=ask.
  Read the matching entry in `.agent-sync/config.json` before each phase;
  it is authoritative if changed since setup. Schema: `modelPolicy[workflowId][agentId]`
  is `"balanced"` (preset: planning=sonnet, implementation=haiku, review=sonnet)
  or `{planning, implementation, review, escalation}` (`auto`|`ask`|`never`);
  a removed policy disables routing. A missing preset definition or invalid entry
  requires correction before dispatch.
  Planning includes discovery, design, plan writing, and substantive replanning.
  Review includes task/spec/code reviews and the final whole-branch review.
  Implementation includes coding and running the plan's checks only after
  the workflow's plan approval/readiness gate is satisfied, with concrete
  scope and acceptance checks. If the plan is absent or materially ambiguous,
  return to planning before implementation. Model choice never skips tests,
  approval gates, or required reviews.
  Use the supported subagent model option or a custom subagent model field when the workflow dispatches a phase. For work in the main session, the user can select the model with /model. Verify host overrides have not forced a different model.
  Keep the workflow's official dispatch, prompts, tools, and reports. Apply
  the model through that dispatch's supported controls; do not replace its
  lifecycle or edit its owned state or installed skills. Do not change
  global/default model settings for all phases.
  If a selected model is unavailable, model selection is unsupported, or
  host overrides conflict, report the requested model and the limitation
  and request a supported replacement or manual switch before that phase.
  Never claim a model switch without host evidence; report the requested
  model and actual model if exposed, otherwise mark actual model unverified.
  If implementation requires redesign or repeats the same failure after two
  attempted fixes, use the planning model for diagnosis/replanning under
  escalation=auto and report why. With escalation=ask, ask first; with
  escalation=never, stop that task and report the blocker. Return to the
  implementation model once the revised plan is ready. Final review always
  uses the configured review model regardless of escalation.
  Record routing decisions in the workflow's normal report if supported, or
  the conversation; keep HANDOFF.md limited to task IDs.
- Read `HANDOFF.md` to see which agent (Codex, Antigravity) last touched
  each plan/task and what's next.
- Before claiming or executing a plan task, check whether the user's prompt
  explicitly names a configured workflow tool or its artifacts. Only then use
  that tool's official lifecycle for the whole task, including its required
  verification and report. A prompt that doesn't mention a workflow tool gets
  a direct, ordinary execution path — do not route it through a workflow tool
  on your own inference.
- Claim a task by adding an entry to `HANDOFF.md`:
  `Claiming plan-name/task-N — claude`
- Before committing, read the convention in `docs/PROJECT_CONTEXT.md`. If it
  names a repository policy file, read that source too. Follow its format and
  examples. Keep plan names, task numbers, agent identity, and AI-attribution
  out of the commit message; workflow state and `HANDOFF.md` retain task
  traceability.
- Do not add a "Co-Authored-By" trailer or AI-attribution footer to
  commits or PRs. If this agent's setup has an equivalent
  auto-attribution behavior, disable it the same way
  `.claude/settings.json` does for Claude Code.
- At the end of a session, append a handoff entry to `HANDOFF.md` with task IDs
  only (e.g. `plan-name/task-N` or `none`). Do not write summaries or progress
  prose here — rich execution details belong in your workflow tool (e.g. `.superpowers/sdd/`).
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
