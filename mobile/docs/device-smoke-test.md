# Device Smoke Test Checklist

Run this checklist on a real device over Tailscale with a running server and at least two scrcpy instances.

## Server Discovery & Login

There is no manual server entry any more — the Connecting screen resolves the server on its own.

- [ ] **Connecting: cold launch on Tailscale finds the local IP via /server-info** — Fresh install (no cached base), Tailscale connected, server running. Launch the app; verify it shows the "Looking for your server…" spinner briefly, then lands on InstanceList without any prompt, and that the "Server" row on InstanceList shows the local Tailscale IP (not the public tunnel host).
- [ ] **Connecting: falls through to public-only with Tailscale off** — Disable Tailscale on the device (server still reachable only via the public tunnel). Launch the app; verify it still reaches InstanceList (via the public URL) after the bootstrap step, and the "Server" row shows the public tunnel host.
- [ ] **Connecting: "Can't find server" with the public URL itself unreachable** — Disconnect the device from all networks (or block both Tailscale and internet). Launch the app; verify the "Can't find server" message appears with a Retry button, and no crash occurs.
- [ ] **Connecting: Retry re-runs discovery** — From the "Can't find server" state above, restore connectivity and tap Retry; verify the spinner reappears and the app proceeds to InstanceList (or Login) without a restart.
- [ ] **Connecting: cached base fast path on a warm launch** — After a successful launch, force-quit and relaunch the app with the server still reachable at the same address; verify it reaches InstanceList quickly without visibly re-running the public-URL bootstrap step (should feel near-instant).
- [ ] **Login: AUTH_TOKEN set on the server routes to the Login screen** — With `AUTH_TOKEN` set on the server, launch the app; verify discovery lands on the Login screen (not InstanceList) and entering the correct token advances to InstanceList.
- [ ] **Login: wrong token shows inline error** — On the Login screen, enter an incorrect token; verify an inline "Invalid token" error appears without crashing and the screen stays put.
- [ ] **Cookie jar: switching to a different host clears the old session** — With `AUTH_TOKEN` set and a valid login session against one server, point the device at a different server host (e.g., a second PC) so discovery resolves to a new host; verify the app does not silently reuse the old session (Login screen appears again for the new host) rather than leaking the prior cookie.

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
