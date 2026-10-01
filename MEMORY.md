# Project Memory &amp; Lessons

<!-- agent-sync:memory:start -->

## Lessons

- Auth Gate: Replacing ACL tables can introduce authorization leaks → Verify replacement handlers check ownership lock (e.g. claim-once-then-lock in `_auth_gate`).
- Signaling Tests: WebSocket client open fires before server accepts connection → Pair connection with `waitForCloseCode` to ensure server accepted token.
- Next.js Export: Client router fetches `<route>.txt` RSC payloads on navigation → Serve `.txt` payloads alongside HTML to prevent hard-navigation 404s.
- Test Coverage: Unwired test suites cause silent regressions → Ensure all new package tests are wired into root npm scripts and CI.

<!-- agent-sync:memory:end -->

