# Expo Native Client Migration — Design

**Date:** 2026-08-14
**Status:** Approved design, pre-implementation
**Author:** brainstorming session

## Summary

Replace the web PWA client (`src/client/`) with a React Native app built on
Expo, targeting iOS and Android. The app streams scrcpy/mediamtx WebRTC video
over Tailscale via WHEP, forwards touch/keyboard input over a WebSocket, and
reaches feature parity with the current web client (instance list, quality
tiers, quick-switch, keyboard).

The Python FastAPI server is **unchanged**. Its `/instances/{serial}/select`
endpoint already returns absolute `whep_url` and `stun_url` derived from
`get_best_ip()` (Tailscale IP), so it is already native-ready.

## Decisions (from brainstorming)

| Question | Decision |
|----------|----------|
| Platforms | iOS + Android |
| Server discovery | Manual base-URL entry, persisted (AsyncStorage) |
| V1 scope | Full parity: list+stream+touch, quality tiers+adaptive, quick-switch+prev/next, keyboard |
| Repo layout | `mobile/` subdir in this repo; web client replaced (not kept in parallel) |
| whep/stun URL derivation | Keep server-derived (Tailscale IP via `get_best_ip()`); **no server change** |
| MJPEG fallback | Dropped. WebRTC-only; on failure show error overlay + Reconnect |
| Workflow | Expo **dev-client** (Expo Go cannot load `react-native-webrtc`) |

## Architecture

### Repo layout

```
window-control/
├── src/                    # Python server — UNCHANGED
└── mobile/                 # NEW Expo app
    ├── app.json
    ├── eas.json
    ├── App.tsx
    ├── package.json
    └── src/
        ├── api/            # ServerContext: base URL + fetch/ws wrappers
        ├── webrtc/         # WHEP negotiation (react-native-webrtc)
        ├── input/          # gesture → normalized coords → WS
        ├── quality/        # adaptive tier sampler
        ├── screens/        # ServerSetup, InstanceList, Stream
        └── components/     # cards, toolbar, drawer, stats, settings
```

### Stack

- **Expo SDK (latest)** + **dev-client** — mandatory for `react-native-webrtc`;
  Expo Go will not work. Build once per platform with
  `eas build --profile development`, then `expo start --dev-client`.
- **`react-native-webrtc`** + **`@config-plugins/react-native-webrtc`** — the
  config plugin wires native permissions and pods.
- **`react-navigation`** (native-stack) — 3 screens: ServerSetup → InstanceList → Stream.
- **`@react-native-async-storage/async-storage`** — persist server base URL and preferred tier.
- **`react-native-gesture-handler`** + **`react-native-reanimated`** — touch, drag,
  swipe-to-switch, drawer.
- **`expo-font`** + **`@expo-google-fonts/archivo`** — bundle the Archivo family
  (400/500/600/700) so the type renders without a runtime web-font fetch.
- **`react-native-svg`** — the v3 UI uses SVG glyphs (logo, chevrons, tab icons,
  toolbar icons, net/error marks) shipped as real vector icons, not unicode.
- **EAS Build** — iOS + Android binaries.

**Workflow change (the biggest one):** dev-client, not Expo Go. Every native
dependency change requires a new dev build.

## Data flow & core modules

### `api/` — ServerContext

Holds the base URL loaded from AsyncStorage. Every request prefixes it.

- `GET  {base}/instances` → instance grid
- `POST {base}/instances/{serial}/select` → `{whep_url, stun_url, serial, name, w, h}`
  — `whep_url`/`stun_url` are already absolute Tailscale URLs; used as-is.
- `POST {base}/instances/{serial}/keyframe` → switch prefetch (fire-and-forget)
- `POST {base}/instances/{serial}/quality` `{tier}` → set tier
- `GET  {base}/instances/{serial}/preview` → thumbnail (Image src, cache-bust `?t=`)
- `WS   {base→ws}/input` → touch/key JSON

Base `http(s)://host` maps to `ws(s)://host` for the WebSocket.

### `webrtc/` — WHEP negotiation

Port of the web `initWebRTC` using `react-native-webrtc` globals
(`RTCPeerConnection`, `RTCView`, `mediaDevices`):

1. `new RTCPeerConnection({ iceServers: [{ urls: stunUrl }] })`
2. `addTransceiver('video', { direction: 'recvonly' })`
3. `createOffer` → `setLocalDescription`
4. **Wait for ICE gathering** — resolve on first `srflx` candidate (fast path),
   else on `complete`, else a hard cap. This is REQUIRED: the offer is
   non-trickle and must carry the srflx candidate that the Tailscale-bound STUN
   reflects. (Same reason as the web client — see the mDNS/STUN notes below.)
5. POST offer SDP to `whep_url` (`Content-Type: application/sdp`)
6. `setRemoteDescription({ type: 'answer', sdp })`
7. `ontrack` → render stream via `<RTCView streamURL={stream.toURL()} objectFit="contain" />`

**ICE failure handling:** honor the tier-switch window (`_tierSwitchUntil`
equivalent) — a tier change restarts scrcpy and bounces ICE transiently; wait it
out before tearing down and re-negotiating. Direct port of the web logic.

**mDNS/STUN carry-over:** the reason the web client waits for a `srflx`
candidate is that iOS Safari only emits an mDNS `.local` host candidate, which
mediamtx cannot resolve over Tailscale; the embedded STUN bound to the Tailscale
IP produces a reachable srflx. `react-native-webrtc` on iOS has the same
constraint, so the srflx-wait logic ports directly and remains necessary.

### `input/` — gesture → normalized coords → WS

- Measure the `RTCView` layout rect (`onLayout`) and the video intrinsic `w,h`
  (from the select response) to replicate `normalizeCoords` letterbox math
  (`object-fit: contain`).
- Gesture Handler over the video:
  - Tap → `click`
  - Pan → `drag_start` / `drag_move` (throttle 16ms) / `drag_end`
  - Two-finger vertical → `scroll` (dy sign)
- Send JSON over `/input` WS using the same message shapes the server already
  parses (`type`, `x`, `y`, `scroll`, `dy`, `key`).

`normalizeCoords` and the letterbox math are pure functions — ported with unit
tests.

### `quality/` — adaptive tier sampler

Direct port of the web adaptive logic:

- `pc.getStats()` every 5s → packet loss, RTT.
- **Downgrade-only** stepping under sustained congestion
  (`loss > 0.08 || rtt > 400`, 3 consecutive samples).
- Tier-switch window suppresses re-negotiation during the scrcpy restart bounce.
- Manual pin wins for 60s; `auto` resumes adaptation.
- Preferred tier persisted in AsyncStorage.

### End-to-end flow

`ServerSetup` saves base URL → `InstanceList` polls `/instances` → tap card
(prefetch keyframe) → `POST /select` → `Stream` mounts PeerConnection →
`RTCView` + gesture layer + `/input` WS.

## Visual design (from Claude Design — "EmuCtrl v3")

Source: Claude Design project `WindowControl Remote Client`, file
`EmuCtrl v3.dc.html`. This **supersedes** the earlier Modernist-dark prototype.
Design language: **light warm ground** with white cards, **soft radii**, a
**coral accent**, pill buttons, and a floating bottom nav — the instance list is
a **scannable vertical list** of full-width previews, not a grid.

**One deliberate split:** the **stream screen stays dark** (`#141110`). Light
chrome behind video costs contrast and glows in a dark room, so on the stream
screen the overlays are **translucent white "glass"** (`rgba(250,248,246,.9)`)
over the dark video.

### Design tokens

| Token | Value |
|-------|-------|
| Font | **Archivo** (weights 400 / 500 / 600 / 700); mono = ui-monospace/Menlo for stats |
| Accent (coral) | `#f2916f` — primary buttons, active states, LIVE pill, stream key |
| Accent hover/ink-coral | `#c96a48` (links) |
| Error | `#c2452a`; error bg `#fbe5de` |
| Bg (app, warm) | `#eae7e3` / screen `#f2f0ed` |
| Surface (cards) | `#ffffff` |
| Card surface (active) | `#fdeee7` |
| Text (ink) | `#1c1a19`; muted `rgba(28,26,25,.45–.6)` |
| Stream bg (dark) | `#141110` |
| Stream glass | `rgba(250,248,246,.9)`; stream text on glass `#1c1a19` |
| Radius | **soft** — inputs/cards 16–26px, phone frame 34px, buttons **pill (999px)** |
| Net dot | connected `#3f9d6d` (chip bg `#e6f2ea`) · connecting `#e0a52c` (chip `#fbf0dc`, blink) · disconnected `#c2452a` (chip `#fbe5de`) |
| Shadows | soft ink-tint, e.g. `0 1px 6px rgba(28,26,25,.06)`, nav `0 6px 22px rgba(28,26,25,.14)` |

Bundle `Archivo` (400/500/600/700) with the app (`expo-font` /
`@expo-google-fonts/archivo`) — no runtime web-font fetch. Idiom: rounded
everything, centered pill primary buttons, generous type hierarchy.

### Per-screen layout (from the v3 prototype)

- **ServerSetup (portrait, light):** large rounded **hero image** filling the
  top → EmuCtrl logo + wordmark → "Control every window, from anywhere" (h3,
  ~27px, 700) → subhead → "Server base URL" label → rounded input (56px, white,
  1.5px border, coral caret; border turns error-red on error) → error card
  (rounded `#fbe5de`, icon + title + hint) when unreachable → **"Start streaming"**
  pill button (coral, centered, inline spinner while connecting).
- **InstanceList ("Windows", portrait, light):** header = logo + "Windows" (h3,
  700) + round white refresh button (spins while refreshing). Body:
  - **Server card** (white rounded): "Server" label + host + a **net chip**
    (dot + Online/Connecting/Offline in the mapped chip colors).
  - "Instances" subhead + `N online` / "Syncing…" on the right.
  - **Instance rows** (vertical list, not grid): each a white rounded card with a
    16:9 rounded preview, then a row of title + optional **LIVE pill** (coral) +
    chevron. Active row = `#fdeee7` bg + coral border. (Per-instance meta
    `WxH · fps · ms` and load% appear in the drawer; the list row shows title +
    LIVE + chevron.)
  - Empty state: white rounded card, framed monitor icon, "No windows found",
    coral Refresh pill.
  - **Floating bottom nav** (white pill bar, 5 tabs): Windows · Stats · **Stream**
    (center coral hero circle) · Server · Setup. See "Bottom nav scope" below.
- **Stream (landscape, dark):** full-bleed letterboxed video (`contain`) on
  `#141110`. Right-edge vertical toolbar on a **white-glass** panel: net dot +
  short label, then icon buttons — settings, switch, keyboard, stats-toggle,
  back; active button = coral fill. Overlays (all white-glass, rounded):
  - **Stats:** top-left rounded glass block, mono text: res/fps/rate/rtt/loss/
    input/jitter/tier.
  - **Keyboard:** bottom, a row of **key pills** (Esc/Back/Home/Tab/Enter) above
    an "INPUT" glass bar with a blinking coral caret.
  - **Quick-switch drawer:** floating rounded white card (left), "Instances"
    header + round close, rows (rounded chip icon + title + meta + optional LIVE
    pill); active row = `#fdeee7` + coral chip.
  - **Settings modal:** centered white rounded card, "Quality" label + 5 **pill**
    tiers (Auto/480p/720p/1080p/1440p, selected = coral fill), a "Show live stats"
    toggle row (rounded, coral track when on), coral "Done" pill.
  - **Error overlay:** dark cover with a centered white rounded card, coral-circle
    broken-signal icon, "Stream unavailable" + explanation, coral Reconnect pill
    (spinner) + neutral "Windows" pill.

### Bottom nav scope (decision)

The v3 bottom nav shows 5 tabs, but v1 scope has only 3 real screens. **Decision:**
render the nav visually; wire **Windows → InstanceList**, **Stream → active
stream**, **Setup → ServerSetup**. **Stats** and **Server** tabs render but are
**no-ops / visibly disabled** in v1 (no new screens — avoids scope creep). They
are placeholders for a later release.

The `.dc.html` prototype is a design artifact (Claude Design `sc-if`/`sc-for`/
`DCLogic` runtime), not shippable RN — it is the visual source of truth the RN
components must match. `image-slot.js`/`support.js` are the design-tool runtime,
not app code.

## Screens

### ServerSetup (new)

- Text input for base URL, Connect button, validation ping (`GET /instances`).
- Persist to AsyncStorage on success.
- Shown on first launch or on connection failure; skipped once a URL is saved
  and reachable.

### InstanceList (= web `screen-list`)

- FlatList grid of instance cards with lazy preview thumbnails
  (`Image`, cache-bust `?t=`), 16:9 thumbnails matching current CSS.
- Poll `/instances` every 60s while focused; pull-to-refresh + refresh button.
- Tap → prefetch keyframe + navigate to Stream.

### Stream (= web `screen-stream`)

- `RTCView` fills the screen, `objectFit="contain"` letterbox.
- Right toolbar overlay: net-status dot, settings, switch, keyboard, back.
- Gesture layer for touch input.
- Settings modal: quality tiers (auto / 480 / 720 / 1080 / 1440) + stats toggle.
- Quick-switch drawer (slide-in) + swipe up/down on the toolbar → prev / next
  instance.
- Stats overlay: w×h, fps, Mbps, net RTT, loss, input RTT, jitter, tier.
- Keyboard: hidden `TextInput`, `onKeyPress` → key events over WS.

## Error handling

- Unreachable server URL → ServerSetup shows error, stays put.
- WHEP negotiate fail → error overlay + Reconnect (retries WHEP). No MJPEG.
- ICE failed → tier-switch-window guard → auto-retry, then error overlay.
- WS `/input` drop → exponential backoff reconnect (1s→30s); net-status dot
  reflects state.
- App foreground (`AppState` → active) → re-check WS + re-negotiate WHEP
  (mirrors the web `visibilitychange` handler).
- Instance disappeared (404 on select) → back to list + refresh.

## Testing

- **Unit (Jest + RTL):** `normalizeCoords` letterbox math, tier-step logic,
  WS message builders, base-URL/ws-URL prefixing. TDD for these pure modules.
- **Component:** ServerSetup validation, InstanceList render/tap, Settings tier
  select.
- **Integration (device-verified, manual checklist):** WebRTC negotiation and
  gestures on a real device over real Tailscale against real scrcpy. Documented
  as a smoke-test checklist.
- **Python server:** unchanged; existing `tests/` stay green.

## Explicit non-goals / feature losses

- **MJPEG fallback dropped.** No native multipart-img equivalent; WHEP+STUN is
  the working path. On WebRTC failure the app shows an error overlay with a
  Reconnect button instead of silently degrading to MJPEG.
- Web PWA (`src/client/`) is replaced, not maintained in parallel.
- No new server endpoints, no server-side URL-derivation change.

## Build & release notes

- Dev builds: `eas build --profile development --platform ios|android`, then
  `expo start --dev-client`.
- Production: EAS Build for store/ad-hoc binaries.
- `mobile/` is versioned alongside the server in this repo (lockstep).
