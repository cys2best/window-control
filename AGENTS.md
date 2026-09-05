# Codex Instructions

<!-- agent-sync:agent-policy:start -->
This file is intentionally thin. All real project knowledge lives in the
shared files below so other agents see the same thing.

See:
- docs/PROJECT_CONTEXT.md — tech stack, conventions, build commands
- HANDOFF.md — the running log between agents, per plan/task

(Codex doesn't support `@path` imports like Claude Code does —
read both files above manually at the start of every session, or wire
this into a startup script if your setup supports one.)

## Codex specific
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
- Read `HANDOFF.md` to see which agent (Claude Code) last touched
  each plan/task and what's next.
- Before claiming or executing a plan task, check whether the user's prompt
  explicitly names a configured workflow tool or its artifacts. Only then use
  that tool's official lifecycle for the whole task, including its required
  verification and report. A prompt that doesn't mention a workflow tool gets
  a direct, ordinary execution path — do not route it through a workflow tool
  on your own inference.
- Claim a task by adding an entry to `HANDOFF.md`:
  `Claiming plan-name/task-N — codex`
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
