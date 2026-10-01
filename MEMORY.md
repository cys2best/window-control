# Project Memory &amp; Lessons

<!-- agent-sync:memory:start -->

## Lessons

- Access Gate: Requests relayed through a local proxy arrive from loopback, which skips pairing → Never front the app with a local relay; keep uvicorn `proxy_headers=False`.
- Secure Storage: expo-secure-store rejects keys outside `[A-Za-z0-9._-]` → Derive per-host keys through `deviceTokenKey`, never from a raw URL.
- Signaling Tests: WebSocket client open fires before server accepts connection → Pair connection with `waitForCloseCode` to ensure server accepted token.
- Next.js Export: Client router fetches `<route>.txt` RSC payloads on navigation → Serve `.txt` payloads alongside HTML to prevent hard-navigation 404s.
- Test Coverage: Unwired test suites cause silent regressions → Ensure all new package tests are wired into root npm scripts and CI.

<!-- agent-sync:memory:end -->

