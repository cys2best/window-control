# EmuCtrl UI/UX Redesign — Design Source (frozen snapshot)

Pulled from the Claude Design project
`claude.ai/design/p/8f7bb09a-8bc6-4744-a24d-206ac3cd5646` on 2026-09-08 so
implementation doesn't need a live `claude-design` MCP call to check the
design. This is a **snapshot**, not a live sync — if the design project
changes after this date, re-pull the relevant file(s) before trusting this
copy.

See the implementation spec at
`docs/superpowers/specs/2026-09-08-emuctrl-ui-redesign-design.md` for how
this maps onto the actual app.

## Files

- `EmuCtrl.dc.html` — main design canvas: sidebar nav/tokens + all locked
  screens (auth, dashboard phone/tablet, stream, account, Windows host
  widget) with inline `SPECS` rationale blocks per screen.
- `EmuCtrl-Logo.dc.html` — logo exploration; the locked mark is variant
  "4b" (screen frame, cyan edge rail, tap dot) per `EmuCtrl.dc.html`'s
  sidebar note ("Logo is final (4b: ...)"). Its inline SVG markup is the
  source for generated app icons/favicon/tray icon.
- `Stream Options.dc.html` — locked stream-screen edge-rail layout spec
  (option 3a), with the design rationale ("WHY FIVE", "SIGNAL HEAD", "SWAP
  IN THE LEFT SAFE ZONE") that the main canvas's stream screen implements.
- `ios-frame.jsx` — device-chrome helper (`IOSDevice`, status bar, nav bar,
  keyboard, list rows) used only to render the design mockup's phone
  frames. Not part of the shippable app — the real app has no pywebview/iOS
  chrome layer.
- `code/StreamScreen.web.jsx`, `code/StreamScreen.native.jsx` — reference
  implementations of the stream screen for web (React + Tailwind classes)
  and native (React Native + `StyleSheet`). These map directly onto
  `packages/ui/src/screens/Stream.tsx` and friends; the native file already
  uses the same styling convention `packages/ui` uses today (no Tailwind),
  so it needs no paradigm translation — only the web file's Tailwind
  classes need converting to inline style objects.
- `support.js` — the design canvas's own runtime (parses `<x-dc>` templates
  into React). Needed only to preview the `.dc.html` files in a browser;
  irrelevant to the real app.
- `uploads/Screenshot 2026-09-06 at 23.35.00.png` — background game-frame
  photo used as mockup filler in the stream screen. **Truncated on pull**
  (source is 2734×1538, exceeds the 256KB single-file fetch cap) — treat as
  reference-only illustration, not a pixel-exact asset. Not a spec
  dependency; the real app renders its own live video/screenshot here.

## Sample data is not real data

Every screen in `EmuCtrl.dc.html` renders illustrative sample data (fake
instance names like `LDPLAYER-01`, a fake signed-in user "Kai Hoang", fake
IPs, randomly-jittered RTT numbers). None of that is meant to ship — the
implementation spec maps each mock value to the real `packages/core`/
Supabase/telemetry source that replaces it.
