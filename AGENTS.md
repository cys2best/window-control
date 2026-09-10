# Agent Instructions (Codex, Antigravity)

<!-- agent-sync:agent-policy:start -->
This file is intentionally thin. All real project knowledge lives in the
shared files below so other agents see the same thing.

See:
- docs/PROJECT_CONTEXT.md — tech stack, conventions, build commands
- HANDOFF.md — the running log between agents, per plan/task

(Codex, Antigravity do not support `@path` imports like Claude Code does —
read both files above manually at the start of every session, or wire
this into a startup script if your setup supports one.)

## Codex, Antigravity specific
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
- For superpowers, when running as codex, use planning=gpt-5.6-sol,
  implementation=gpt-5.6-luna, review=gpt-5.6-sol,
  escalation=ask. This policy applies only when running as codex.
  Read the matching entry in `.agent-sync/config.json` before each phase;
  it is authoritative if changed since setup. Schema: `modelPolicy[workflowId][agentId]`
  is `"balanced"` (preset: planning=gpt-5.6-sol, implementation=gpt-5.6-luna, review=gpt-5.6-sol)
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
  Use an exposed subagent model override or the workflow's configured custom agent model when supported. For main-session work, request the host's model picker or a new CLI session with -m MODEL. A Markdown instruction alone does not change the running model.
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
- For superpowers, when running as antigravity, use planning=gemini-flash3.7,
  implementation=gemini-flash3.7, review=gemini-flash3.7,
  escalation=ask. This policy applies only when running as antigravity.
  Read the matching entry in `.agent-sync/config.json` before each phase;
  it is authoritative if changed since setup. Schema: `modelPolicy[workflowId][agentId]`
  is `"balanced"` (preset: planning=gemini-flash3.7, implementation=gemini-flash3.7, review=gemini-flash3.7)
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
  Use gemini-flash3.7 for every workflow phase through Antigravity's exposed model controls. Treat this as a configured model ID, not a Gemini CLI alias. Verify availability in the current host; if unavailable or not selectable, request a supported replacement or manual switch rather than silently substituting another model.
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
- Read `HANDOFF.md` to see which agent (Claude Code) last touched
  each plan/task and what's next.
- Before claiming or executing a plan task, check whether the user's prompt
  explicitly names a configured workflow tool or its artifacts. Only then use
  that tool's official lifecycle for the whole task, including its required
  verification and report. A prompt that doesn't mention a workflow tool gets
  a direct, ordinary execution path — do not route it through a workflow tool
  on your own inference.
- Claim a task by adding an entry to `HANDOFF.md` using your active agent identifier:
  `Claiming plan-name/task-N — <agent-id>` (use codex/antigravity depending on which agent you are running as)
- Before committing, read the convention in `docs/PROJECT_CONTEXT.md`. If it
  names a repository policy file, read that source too. Follow its format and
  examples. Keep plan names, task numbers, agent identity, and AI-attribution
  out of the commit message; workflow state and `HANDOFF.md` retain task
  traceability.
- Do not add a "Co-Authored-By" trailer or AI-attribution footer to
  commits or PRs. Disable auto-attribution in your respective agent config.
- At the end of a session, append a handoff entry to `HANDOFF.md` with task IDs
  only (e.g. `plan-name/task-N` or `none`). Do not write summaries or progress
  prose here — rich execution details belong in your workflow tool (e.g. `.superpowers/sdd/`).
<!-- agent-sync:agent-policy:end -->
