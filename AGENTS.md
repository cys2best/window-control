# Agent Instructions (Codex, Antigravity)

<!-- agent-sync:agent-policy:start -->
This file contains shared project knowledge, conventions, and agent instructions.

See:
- MEMORY.md — project learnings, component pitfalls, and durable lessons
- COMMIT_CONVENTION.md — commit message format and rules
- AGENTS.local.md — local private notes and personal overrides (gitignored)

## Overview

Stream specific Windows 11 application windows to your iPhone over Tailscale.

## Commands

- Verify: `uv run pytest tests/ -v`
- Focused test: `uv run pytest tests/ -k "<name>"`

## Boundaries

- `CLAUDE.md`: minimal pointer to `AGENTS.md`; shared project knowledge and agent instructions belong here.
- `MEMORY.md`: durable project lessons and component pitfalls.
- `COMMIT_CONVENTION.md`: Conventional Commits format and rules.
- Vendor exclusions: Ignore dependencies and build artifacts under `.venv/`, `build/`.
- Vendor exclusions: Ignore dependencies and build artifacts under `.venv/`, `build/`.

## Conventions

- Commit format: `<type>(optional-scope): imperative description` (see `COMMIT_CONVENTION.md`)
- Commit example: `feat(auth): add token refresh`
- If a plugin workflow (e.g. Superpowers) owns task state, continue through its own command; never hand-edit its state files.
- Otherwise, build features and fix bugs by following `.agent-sync/TDD.md` (todo list, then red → green → refactor per task).

## Claude Code, Codex, Antigravity specific
- Shared memory (agent-mem): a short digest of recent sessions from every agent is injected at session start. Don't re-explore work it already covers; search with `/agent-sync:mem-search` before re-reading large files or re-running long investigations.
- Resuming: if the digest warns that a session was interrupted, or the user says continue/resume work another agent started, run `/agent-sync:resume` first and continue its in-progress step instead of starting over.
- Before committing, follow the commit convention in `COMMIT_CONVENTION.md`. Keep plan names, task numbers, agent identity, and AI-attribution out of the commit message.
- Do not add a "Co-Authored-By" trailer or AI-attribution footer to
  commits or PRs. Disable auto-attribution in your respective agent config.
- Think Before Coding: State consequential assumptions and tradeoffs; ask when ambiguity changes the result, and suggest a simpler approach when appropriate.
- Simplicity First: Implement only the requested behavior with the smallest clear solution; avoid speculative features, configuration, and abstractions.
- Surgical Changes: Match local style, change only what the task requires, and remove only code made unused by your changes; flag unrelated cleanup separately.
- Learning: Keep up to five one-line lessons (25 words each) in MEMORY.md as `Component: pitfall → action`; merge duplicates, replace obsolete entries, and prefer regression tests.
- Context upkeep: Aim for 80 lines, at most 120 lines and 1,200 words; keep actionable facts once, link to existing detail, and omit history, progress, and empty sections.
<!-- agent-sync:agent-policy:end -->
