# Unified React/React Native Frontend — Design

## Problem

Web (`src/client/*.js` — vanilla JS/HTML/CSS) and mobile
(`mobile/src/*.tsx` — Expo/React Native) duplicate the same concerns in
two languages/frameworks: instance list, WHEP/engine session lifecycle,
quality-tier logic, input-channel protocol, Supabase auth. Every layout
change or core-logic fix is done twice. Desktop is not a separate
codebase today — `src/gui/tray.py` (pystray) just opens the system
browser to the local web PWA.

## Goal

One shared codebase for the logic and UI duplicated across platforms,
while keeping each platform's app idiomatic (real Next.js routing/SEO
for web, real Expo for mobile). Desktop becomes an embedded window
(pywebview) instead of a browser tab, reusing the web build directly.

## Non-goals

- `src/server/` (FastAPI) API surface, auth, WHEP signaling endpoints:
  unchanged.
- `engine/` C++ WebRTC engine: unchanged.
- `infra/` VPS/coturn/signaling: unchanged.
- No incremental strangler rollout — single cutover (see Migration).

## Architecture

Monorepo, npm workspaces (matches existing tooling — `mobile/` and
`infra/vps/signaling/` both already use npm; no new package manager
introduced):

```
apps/
  web/        — Next.js app (replaces src/client/*)
  mobile/     — existing Expo app, relocated, refactored onto packages/core
  desktop/    — pywebview shell (Python) + tray.py, points at the served web build
packages/
  core/       — shared TS: API client, WHEP/engine-session state machine,
                input-channel protocol, quality tiers, Supabase auth —
                seeded from mobile/src/{webrtc,input,quality,api}
  ui/         — shared React components (react-native-web), seeded from
                mobile/src/{screens,components}
```

### packages/core

Seeded from mobile's existing TS (already tested, already
platform-abstracted where it matters): `webrtc/whep.ts`,
`input/inputChannel.ts`, `quality/{tiers,adaptive}.ts`,
`api/{client,urls,supabaseAuth,ServerContext}.ts`. These move as-is;
web's untested vanilla-JS equivalents (`engine_session.js`,
`input_channel.js`, parts of `app.js`) are retired, not ported forward.

One real platform split remains: `react-native-webrtc` (mobile) and
the browser's native `RTCPeerConnection` (web) are not API-compatible.
`packages/core` defines a `WebRTCAdapter` interface; each app supplies
its own implementation. Everything above that line — session
lifecycle, reconnect/quality-switch logic, protocol framing — is one
shared implementation with one test suite.

### packages/ui

Mobile's existing screens (`InstanceList`, `Stream`, `Login`,
`ServerSetup`, `SettingsModal`, etc.) and components move here, built
with `react-native-web` so `apps/web` can render them directly. The
just-redone dark theme (`src/client/style.css`, commit `93d1844`) is
rebuilt here as the canonical theme (RN `StyleSheet`/design tokens) —
shared source instead of duplicated CSS.

### apps/web

Next.js app-router shell wiring `packages/ui` screens to routes, plus
the browser `WebRTCAdapter`. FastAPI's static mount
(`src/server/app.py`, `StaticFiles(directory=CLIENT_DIR)`) swaps from
serving `src/client/` to serving this app's static export. Cache-busting
via `VERSION` in `src/config.py` stays the same mechanism.

### apps/mobile

Existing Expo app, relocated into the monorepo, refactored to import
from `packages/core`/`packages/ui` instead of its local
`src/{webrtc,input,quality,api,screens,components}`. Its own jest suite
stays, now exercising the shared packages through the app rather than
local duplicates.

### apps/desktop

`src/gui/tray.py` moves here unchanged (still pystray). `pywebview`
replaces "open system browser" with an embedded window pointed at the
same build FastAPI already serves locally — no separate desktop bundle
of the web assets, no new packaging pipeline. PyInstaller + Inno Setup
remain the installer; this is a Python-side dependency add
(`pywebview`), not a new toolchain.

## Migration

Single cutover, not incremental/strangler: one plan builds
`packages/core` + `packages/ui` + `apps/web` to full feature parity
with today's `src/client`, ports `apps/mobile` onto `packages/core`,
wires `apps/desktop`'s pywebview shell — all verified together — then
`src/client/` and mobile's local duplicated modules are deleted in one
commit.

## Testing & CI

- `packages/core`, `packages/ui`: jest (matches mobile's existing
  `jest-expo` setup).
- `apps/web`: same jest config extended for Next.js; Playwright if the
  web-specific `WebRTCAdapter` path needs browser-level coverage jest
  can't give it.
- `apps/mobile`: existing jest suite, now importing shared packages.

This repo has a documented precedent for test rot: `tests/client/*.test.js`
was never wired into CI (only documented as a manual command in
`docs/PROJECT_CONTEXT.md`), and a field rename silently dropped it from
25/25 to 14/25 passing, undetected until a final whole-branch review
(see `docs/PROJECT_CONTEXT.md`'s "Things NOT to do" section). The new
`packages/core`/`packages/ui`/`apps/web` suites must be wired into a
real CI job (`.github/workflows/`) from the commit that adds them —
not left as a documented convention.

## Open questions for the implementation plan

- Exact Next.js routing structure for `apps/web` (maps to mobile's
  `navigation/Root.tsx` stack — needs a routing library decision, e.g.
  hand-rolled Next.js routes vs a cross-platform nav layer).
- Whether `apps/desktop`'s pywebview window needs any native menu/window
  chrome beyond what the tray already provides.
- CI job structure: one workflow per app, or one shared
  `packages/*` job feeding into `apps/web`/`apps/mobile` build jobs.

These are implementation-plan-level decisions, not architectural ones —
deferred to `writing-plans`.
