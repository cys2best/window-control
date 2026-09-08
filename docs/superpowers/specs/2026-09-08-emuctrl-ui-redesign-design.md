# EmuCtrl UI/UX Redesign — Design

## Problem

The shared theme in `packages/ui/src/theme/tokens.ts` — the single visual
source for both `apps/web` and `apps/mobile` (`apps/web` carries no styling
of its own; it just imports screens from `@wc/ui`) — is a light coral/cream
consumer-app palette (accent `#f2916f`, bg `#eae7e3`, Archivo font). It does
not match the product's intended identity: a dark, cyber-tactical HUD
aesthetic, now specified in a Claude Design project
(`claude.ai/design/p/8f7bb09a-8bc6-4744-a24d-206ac3cd5646`, files
`EmuCtrl.dc.html`, `Stream Options.dc.html`, `EmuCtrl Logo.dc.html`, plus
reference implementations `code/StreamScreen.web.jsx` /
`code/StreamScreen.native.jsx`). Only the stream video backdrop is currently
dark; auth, dashboard, modals, drawers, and nav are all light-mode. No
Account/Settings screen exists yet. `apps/desktop`'s Windows host widget
(`src/gui/launcher.py`, PyQt5) is already dark and card-based but uses an
unrelated slate/green/teal/amber palette and system fonts, not the target
tokens.

## Goal

Reskin the app end-to-end (`packages/ui` screens/components, `apps/web`,
`apps/mobile`, `apps/desktop`'s host widget, and app icons/favicon/tray icon)
to match the locked design exactly: token palette, typography, the stream
screen's edge-rail layout, and a new Account screen — while every value shown
on screen continues to come from the app's real state (`packages/core`,
Supabase auth, live instance/telemetry data), never from the design mockup's
illustrative sample data (fake instance names, fake user "Kai Hoang", fake
IPs, random RTT numbers).

## Non-goals

- No new styling framework (Tailwind/NativeWind) — `packages/ui` keeps its
  existing inline-style-object convention; `apps/desktop` keeps Qt
  stylesheets (QSS). Confirmed the design's own native reference
  (`StreamScreen.native.jsx`) already uses plain RN `StyleSheet.create`, the
  same paradigm `packages/ui` uses today — no paradigm gap to bridge.
- No change to `packages/core` business logic, WebRTC session handling,
  auth flow, or API surface — this is a visual/layout pass over existing
  data flows, with narrow, additive exceptions called out per-screen below
  where the design references a value the app has no state for yet (e.g.
  GPU label, stream-defaults settings) — those are added as minimal new
  state, never faked.
- No change to `engine/` (C++), `infra/` (Terraform/VPS), or backend
  (`src/server/`) routes/logic.
- No unrelated refactoring of files this pass doesn't touch.

## Design Tokens (`packages/ui/src/theme/tokens.ts`)

Replace the `theme` object's values in place; extend with new keys where the
design introduces a role the current theme has no equivalent for. Do not
rename keys that already have a direct equivalent, to minimize consumer
churn.

- `theme.color.bg` (canvas): `#eae7e3` → `#06070b`.
- New `theme.color.surface` (`#090a0f`, chrome/sidebar level) and
  `theme.color.surfaceRaised` (`#13161f`, cards/modals/inputs) — the current
  theme has one flat card surface; the design uses two levels.
- `theme.color.border` (hairline): → `#222738`.
- `theme.color.text` (primary ink): `#1c1a19` → `#E6EAF2`. New
  `theme.color.textMuted` (`#7A8496`) and `theme.color.textDim` (`#5C6679`,
  kickers/labels) — the design uses three ink weights.
- `theme.color.accent`: coral `#f2916f` → cyan `#00E5FF`.
- New `theme.color.live` (tangerine `#FF5722` — live/recording/destructive
  actions) and `theme.color.telemetry` (mint `#10B981` — healthy
  signal/stats), replacing the ad-hoc hex literals currently inlined in
  `Login.tsx` and badge components.
- `theme.font.ui`: Archivo → Space Grotesk (400/500/600/700).
- `theme.font.mono`: generic monospace → JetBrains Mono (400/500/700).
- Mobile: swap the `@expo-google-fonts/archivo` loader in `apps/mobile/App.tsx`
  for `@expo-google-fonts/space-grotesk` + `@expo-google-fonts/jetbrains-mono`.
- Web: add Google Fonts `<link>` tags (preconnect + stylesheet, matching the
  design's own `<link>` setup) to `apps/web/src/app/layout.tsx`'s head; update
  its `themeColor` from `#000000` to `#06070b`.
- Drop the coral/cream tokens entirely — full swap, no legacy light theme
  retained.

## Screens & Components (`packages/ui`)

### 1. Auth (`screens/Login.tsx`)

Layout: single column, 24px gutters, flush-left — mark → headline →
connectivity chips → mode switch → fields → security notice → CTA.

- Connectivity chips (new UI element): LAN chip (mint dot, 2s pulse,
  `LAN · {ip}`) and relay chip (neutral until failover, then tangerine).
  Source real reachability/discovery state already available via
  `ServerContext`/host-discovery flow; if no such signal currently exists
  pre-auth, add the minimal check needed rather than hardcoding a chip
  state.
- Sign In / Create Account: 4px-inset segmented switch on `surfaceRaised`,
  active pill cyan bg + canvas text.
- Fields: `surface` bg, 1px hairline, 11px radius, 50px height; focus =
  cyan border + glow (native: shadow props as the closest RN equivalent to
  the web `box-shadow` ring — not pixel-identical across platforms, and
  that's expected).
- Password: mono `SHOW`/`HIDE` text toggle, no icon.
- Create mode only: host pairing code field, mono, letter-spaced.
- Claim-host notice: tangerine 2px left border on `surfaceRaised`, not a
  filled warning block.
- Primary CTA: cyan bg, canvas text, glow shadow, 54px height.

### 2. Dashboard (`screens/InstanceList.tsx`)

- Host status card: ping (largest mono figure, 18px), address, and any
  additional field only if a real data source backs it (omit fields like
  GPU label if `packages/core` has no such source — do not fabricate).
- Instance cards: 16:9 real screenshot/placeholder, scanline animation on
  live instances, tangerine pulsing LIVE badge bound to actual live state,
  res/fps chips, all from `client.instances()` (`packages/core/src/api/client.ts`).
- Capsule bottom nav: Instances / center cyan resume-stream action / Health.
- Tablet/web variant: auto-fill grid, `minmax(260px,1fr)`.

### 3. Stream (`screens/Stream.tsx` + `StreamToolbar`, `SettingsModal`,
`SwitchDrawer`, `StatsOverlay`)

Port the edge-rail layout and interaction model from the design's reference
implementations (`code/StreamScreen.web.jsx`, `code/StreamScreen.native.jsx`),
translated into `packages/ui`'s existing inline-style-object convention
(the native reference already matches this convention; the web reference
uses Tailwind classes and needs translating to inline styles, since
`apps/web` carries no Tailwind today):

- Right edge-rail dock, 68px wide, full height, squared (no radius), one
  hairline on the inner edge, no shadow: KEYS / HOME / RECENT / SET keys at
  52px with 8px gaps, EXIT pinned to the bottom behind its own rule
  (tangerine on press). Auto-collapses after ~4s idle (already implemented
  in the web reference as `IDLE_COLLAPSE_MS`).
- Signal head atop the rail: 4-bar meter driven by real RTT/loss thresholds
  (4 mint bars <25ms, 3 amber <60ms, 2 tangerine beyond, 1 flashing on
  transport loss), replacing `NetChip`/`NetDot`'s green/amber/red dot
  everywhere it's used. ms figure in dim mono underneath. Tapping opens the
  diagnostic HUD.
- Left-gutter SWAP button, 68px, squared, zero video pixels, opens the
  quick-switch drawer; vertical swipe on the same edge cycles instances
  without opening the drawer.
- `SettingsModal`: quality tier as a segmented control bound to real
  `quality/tiers.ts` `TIER_ORDER` (not the mock's fixed list, if they
  diverge), plus existing diagnostic HUD / haptics / gamepad toggles
  restyled to the design's track/knob spec.
- `SwitchDrawer`: bottom sheet, hairline instance cards with live badges,
  bound to `client.instances()`.
- `StatsOverlay`/HUD: decode/network/input/jitter/bitrate mono rows bound to
  real `telemetry`/`onInputRtt`/`EngineSession` state — never the mock's
  static placeholder numbers.

### 4. Account (new `screens/Account.tsx`)

Net-new screen (design's screen 07, not present in the app today):

- Identity block: avatar placeholder, real signed-in user's display
  name/email (Supabase auth session — no placeholder name), ghost "EDIT"
  affordance.
- Stream-defaults and Host & Network row blocks: bound to real settings
  state; where `packages/core` has no backing state yet for a row the
  design shows, add the minimal state needed rather than rendering a
  non-functional row.
- Session block: sign-out row (tangerine text + tangerine hairline,
  states the consequence per the design copy), calling the real
  `clearAuth()` path.
- Wire into `apps/web` as a new `/account` route (thin wrapper matching the
  existing `login`/`instances`/`stream` page pattern) and into
  `apps/mobile`'s `src/navigation/Root.tsx`.

## Desktop Host Widget (`src/gui/launcher.py`, PyQt5/QSS)

- Add a small Python color-constants module mirroring the token palette
  (canvas/surface/surfaceRaised/hairline/ink/textMuted/textDim/cyan/
  tangerine/mint) and swap all QSS color literals to reference it —
  replacing the current slate/green/teal/amber palette.
- Bundle Space Grotesk + JetBrains Mono TTFs (Qt cannot reliably pull Google
  Fonts at runtime) and register them via `QFontDatabase`; apply the UI font
  to labels and the mono font to numeric/IP/ping fields, replacing the
  current system-font stack.
- Add a Fluent-aligned account strip under the caption bar (cyan-initials
  avatar, name, mono email+role, truncating from the right) per the
  design's "widget" spec, bound to the real Supabase auth state already
  shown today. Sign-out button: Fluent secondary style, turns system red
  `#C42B1C` on hover (the one deliberately non-cyan destructive action,
  matching Windows' own chrome).
- `apps/desktop/tray.py`: replace the fallback blue-square icon with the
  real mark (see Brand Assets below); keep existing tray menu/behavior
  unchanged.

## Brand Assets

- Source: the locked mark (design's "4b" variant — screen frame, cyan edge
  rail, tap dot) already exists as inline SVG markup within
  `EmuCtrl.dc.html`'s sidebar/auth-screen icon. Extract it once as a
  standalone SVG file, single source for all generated assets.
- Generate: `apps/web` favicon + PNG icon set; `apps/mobile`'s `app.json`
  icon/adaptive-icon/splash; `apps/desktop` tray icon (ico/png) replacing
  the current fallback blue square.

## Testing & Verification

- `npm run test:core`, `npm run test:ui`, `npm test -w apps/web` after each
  component/screen change.
- Manual: `npm run build -w apps/web && uv run python src/main.py` to eyeball
  the web build; Expo (`apps/mobile`) on simulator/device — no local test
  files exist there by project convention (moved to `packages/core`/`ui` in
  the 2026-09-05 cutover), so manual verification is the only check for
  mobile-specific rendering.
- `uv run pytest apps/desktop/ -v` after the widget reskin, plus a manual
  run for visual confirmation (full Fluent-hover/theme fidelity is
  Windows-only; Mac run confirms logic/layout, not exact native chrome).
- Acceptance: each screen matches its corresponding SPECS block in
  `EmuCtrl.dc.html` (auth / dash / stream / account / widget) and the locked
  rail spec in `Stream Options.dc.html`, with every displayed value traced
  to a real `packages/core`/Supabase/telemetry source rather than the
  mockup's sample data.

## Migration & Rollout

- Single pass across `packages/ui` (propagates to both `apps/web` and
  `apps/mobile` since both consume it), `apps/desktop`, and brand assets —
  no incremental/flagged rollout, per explicit scope decision.
- Work happens on the current branch (`feature/engine`), per explicit
  instruction — not split to a separate branch.
- Sequencing: tokens (blocking) → screens (auth, dashboard, stream+related
  components, new Account) → `apps/web`/`apps/mobile` wiring (routes, font
  loaders) → `apps/desktop` widget (independent, can run in parallel) →
  brand assets (low risk, can run anytime, natural to finish last).

## Technical Debt & Future Work

- The Qt widget's color/font constants and `packages/ui`'s TS tokens are two
  independently maintained sources of the same palette (Approach 2 from
  brainstorming — a codegen'd shared token source — was considered and
  deferred as premature for a one-time reskin). If the palette changes again
  later, both need updating by hand.
- Native focus-ring styling (RN shadow props approximating the web CSS
  glow ring) will not be pixel-identical between web and native; acceptable
  per-platform approximation, not a defect to chase.
