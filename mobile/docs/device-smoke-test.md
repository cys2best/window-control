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

## Public WebRTC Fallback

Requires a server configured with `PUBLIC_UI_URL`, `AUTH_TOKEN`, and `TURN_HOST` (so `ice_servers` actually includes a TURN entry), plus a phone that can be taken off Tailscale (cellular data, not the same Wi-Fi as the dev machine).

- [ ] **Off-Tailscale/cellular: fallback actually connects** — Disable Tailscale on the device (or switch to cellular data), select an instance. Verify direct WHEP fails/times out quickly and the public signaling path (`connectPublicWhep`) takes over, video plays within a few seconds — not hung indefinitely, and the stream is NOT hidden behind the ErrorOverlay once it connects.
- [ ] **On-Tailscale: direct path still wins with no added latency** — With Tailscale/Wi-Fi connected, select an instance. Verify direct WHEP connects and the video paints with no perceptible extra delay versus before this feature existed — the ~2.5s fallback timer must not be felt on the working local case.
- [ ] **Exactly one connection ends up active after fallback (no leftover direct attempt)** — After an off-Tailscale fallback connects, check the toolbar's net indicator settles on a single steady "LIVE" state (no flicker between two competing sessions), and confirm server-side (`/instances` or logs) that the abandoned direct WHEP session was actually torn down (DELETEd), not left as a zombie writing into a full queue.
- [ ] **Real interop check against `signaling_bridge.py`** — On the same server/network, compare the mobile public path's connection against the web client's (`app.js`'s `initWebRTCPublic`) on the same instance: both should negotiate through the VPS relay the same way (WS handshake, raw SDP offer/answer, ICE via the configured TURN server) and both should end up with a playable stream. This is the one check that actually proves the mobile port interops with the real relay, not just a mock.
- [ ] **Fast direct-WHEP failure triggers immediate fallback, not the full timer** — Off-Tailscale, where direct WHEP typically fails within milliseconds (unreachable CGNAT/local address), verify (via device logs, `[stream] trying public path +Nms`) that the fallback starts close to 0ms after `select()`, not after waiting out the full ~2.5s timer.
- [ ] **No signaling_url configured: clean "Disconnected" state, not a hang** — Against a server with no `VPS_SIGNALING_URL` set, force a direct-WHEP failure (e.g. Tailscale off). Verify the app settles on the ErrorOverlay/"Disconnected" state rather than spinning forever, and that reconnect/back both still work normally.

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
