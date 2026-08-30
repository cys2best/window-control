# Handoff Log

<!-- agent-sync:handoff-template:start -->
<!-- Newest entry on top. Each agent appends one entry at session end,
     and one "Claiming" line when picking up a task. Multiple plans can
     appear here at once — always include the plan name. -->

Plan/task identifiers belong here and in workflow state, not in commit subjects.

## Template for new entries
```
### YYYY-MM-DD HH:MM — [claude|codex]
- Claiming: plan-name/task-N (if starting new work)
- Finished: plan-name/task-N, other-plan/task-M
- Next: plan-name/task-K is ready, depends on plan-name/task-N
- Blockers: none / describe
```
<!-- agent-sync:handoff-template:end -->

---
