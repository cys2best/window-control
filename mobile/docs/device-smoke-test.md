# Device Smoke Test Checklist

Run this checklist on a real device over Tailscale with a running server and at least two scrcpy instances.

## Server Setup

- [ ] **ServerSetup: bad URL shows inline error** — Enter an invalid URL (e.g., `http://invalid`) and verify the error appears inline on the screen without crashing.
- [ ] **ServerSetup: valid URL persists and advances** — Enter a valid Tailscale URL (e.g., `http://100.x.x.x:8080`) and verify it's saved to AsyncStorage and navigates to the InstanceList screen.
- [ ] **ServerSetup: unreachable host shows "Can't reach server"** — Enter a syntactically valid but unreachable IP (e.g., `http://100.64.0.1:8080`) and verify the "Can't reach server" error overlay appears.

## Instance List

- [ ] **InstanceList: cards render with live 16:9 previews** — On the InstanceList screen, verify each instance card displays a preview thumbnail in 16:9 aspect ratio that updates periodically.
- [ ] **InstanceList: N-online count correct** — Verify the header shows the correct count of online instances (should match scrcpy instances running on the server).
- [ ] **InstanceList: pull-to-refresh updates** — Pull down on the list and release; verify the preview thumbnails refresh and the instance list updates.
- [ ] **InstanceList: 60s poll refreshes** — Wait 60 seconds without interacting; verify the list updates automatically with new preview data.

## Stream Screen

- [ ] **Stream: WHEP connects; video paints; net dot green** — Tap an instance to open the Stream screen. Verify the WebRTC connection establishes (green network dot in the toolbar), and the video stream displays.
- [ ] **Stream: tap registers on device** — Tap on the video to verify a tap registers on the target device (e.g., opens a menu or confirms selection).
- [ ] **Stream: drag moves** — Drag your finger across the video to verify movement gestures register on the device (e.g., panning or sliding UI).
- [ ] **Stream: two-finger scroll scrolls** — Use two fingers to scroll on the video; verify scrolling actions work on the device.
- [ ] **Stream: keyboard button forwards keystrokes** — Tap the keyboard icon in the toolbar and type text; verify keystrokes are sent to the device and appear in an active text field.

## Settings & Quality

- [ ] **Settings: pinning 480/1080 changes resolution** — Tap Settings, pin the quality to 480p or 1080p, and verify the stream video resolution changes immediately.
- [ ] **Settings: Auto resumes adaptation** — Disable the pin (select "Auto"), and verify the client resumes adaptive quality switching based on network conditions.
- [ ] **Under induced congestion, tier steps DOWN only (never restart-storms up)** — Induce network congestion (e.g., throttle bandwidth in device settings or use a network limiter). Verify the quality tier steps down smoothly without restarting the stream repeatedly.

## Instance Switching

- [ ] **Quick-switch drawer switches instances; new stream paints quickly (keyframe prefetch)** — While streaming, open the quick-switch drawer (swipe from the left or tap the drawer icon), select a different instance, and verify the new stream displays quickly with a keyframe already present (no long buffering).

## Error Recovery

- [ ] **Kill server → ErrorOverlay appears; restart → Reconnect recovers** — Stop the server and verify the ErrorOverlay appears on the mobile client. Restart the server and tap Reconnect; verify the app re-establishes the connection.
- [ ] **Background/foreground the app → WS + WHEP recover** — While streaming, send the app to the background (home button). Bring it back to the foreground and verify both the WebSocket and WHEP connection recover without manual action.

## Tier-Switch ICE Bounce Verification

- [ ] **Verify the tier-switch ICE bounce does NOT blank to the error overlay** — While streaming, trigger a quality tier change (induce congestion or manually pin/unpin quality). Verify the ICE connection bounces (you may see momentary video freeze or flicker) but does NOT show the ErrorOverlay. If it blanks to ErrorOverlay during tier switch, port the `_tierSwitchUntil` suppression window from the web client (`app.js oniceconnectionstatechange`) into `whep.ts` / `Stream.tsx` and re-run this check.

---

## Notes

- All tests assume Tailscale connectivity and a running server with at least two active scrcpy instances.
- Network-induced failures (congestion, timeout) should show error overlays; resolving the network should recover gracefully.
- Any crashes or hangs not covered by the checklist should be logged as a bug with a device stack trace (use Xcode Console for iOS or `adb logcat` for Android).
