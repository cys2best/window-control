# TDD Workflow

Default workflow for features and bug fixes when no plugin workflow (e.g. Superpowers) is in use. Edit this file to fit the project; setup never overwrites it.

Skip it for changes with no behavior to test: typos, docs, comments, formatting, config values.

## Phase 1 — Todo list

1. Break the request into small, sequential tasks. Each task adds or changes one behavior that a single test can prove.
2. For a bug fix, the first task is a test that reproduces the bug.
3. Save the list to `.agent-sync/todo/<feature-slug>.md` as a checklist with the request at the top, so another agent on this machine can continue it if this session stops (the folder is gitignored):
   ```markdown
   # <feature>
   Request: <one-line summary of what the user asked for>

   - [ ] 1. <task>
   - [ ] 2. <task>
   ```
4. Show the list and wait for the user's approval before writing any code.

## Phase 2 — Loop, for each task in order

1. **RED:** Write one test for this task. Run it with the project's test command (see `AGENTS.md` → Commands) and confirm it fails for the expected reason, not a typo or import error. Show the failing output.
2. **GREEN:** Write the minimum code that makes that test pass. Run the test suite and confirm everything passes.
3. **REFACTOR:** Remove duplication and smells in the code touched by this task. Re-run the tests; they must stay green.
4. **Check off** the task in the todo file (`- [x]`) and show the updated list.
5. **Pause** and wait for the user's go-ahead before the next task. If the user says "run all", continue through the remaining tasks without pausing, but still stop when a test fails unexpectedly or the plan needs to change.

If a task turns out wrong or too big, stop, update the todo list, and get approval again before continuing.

## Phase 3 — Finish

1. Run the full test suite (and lint/build if the project has them) and show the result.
2. Summarize what changed and anything left undone.
3. Delete the todo file once every task is checked off, unless the user wants to keep it.
