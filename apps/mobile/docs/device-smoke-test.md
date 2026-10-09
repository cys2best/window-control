# Mobile Device Smoke Test Checklist

Run on a physical iPhone with a development/native build containing
`react-native-webrtc`; Expo Go cannot establish native WebRTC acceptance. Record
the exact mobile build ID, Windows artifact ID and tested SHA in the
[shared remote matrix](../../../docs/testing/shared-remote-validation.md).
No physical iPhone or mobile build ID was supplied for the October 9 validation;
all device rows remain untested. Source Jest results are separate evidence.

For public remote cases disable Tailscale on both endpoints, make no router
changes and use the owner-authorized remote QR/link plus six-digit code. No app
account or login is introduced. Retain LAN pairing coverage separately.

---

## 1. Owner pairing and saved identity
- [ ] Open the PC owner's QR/link, enter its six-digit code and reach the instance list.
- [ ] Confirm the code expires at 300 seconds, is single-use and is invalidated after five wrong attempts.
- [ ] Force quit and reopen the app; reconnect using the saved device token.
- [ ] Pair a second independent PC concurrently; wrong/revoked tokens,
  cross-installation sessions, pairing replay, stale revisions and arbitrary
  forwarding reject without affecting the other PC.
- [ ] With remote service absent, local pairing and LAN access remain usable.

---

## 2. Instance List
- [ ] **Instance Cards with 16:9 Previews**:
  - Verify each active emulator card displays a 16:9 preview thumbnail that updates periodically.
- [ ] **Online Instance Count**:
  - Verify header indicates correct count of running emulator instances.
- [ ] **Pull-to-Refresh**:
  - Pull down on the instance list and release; confirm thumbnails and list refresh cleanly.
- [ ] **60-Second Background Polling**:
  - Leave list idle for 60 seconds without interaction; confirm list auto-refreshes.

---

## 3. Dual-Transport WebRTC Streaming
- [ ] **Stream Connection & Video Display**:
  - Tap an instance card.
  - **Pass condition**: Local WHEP and remote admission retain their separate
    authorization paths. Observe the actual selected direct or TURN route;
    a public WSS signaling connection is not evidence of relayed media.
- [ ] **Touch Tap Registration**:
  - Tap on the stream video; confirm tap registers accurately at the matching coordinates on the remote emulator.
- [ ] **Rapid Drag & Release**:
  - Drag rapidly across the screen, then lift finger or let iOS swipe gesture activate (e.g. Control Center).
  - **Pass condition**: Remote drag tracks smoothly and releases immediately on finger lift (no sticky held touches).
- [ ] **Two-Finger Proportional Scrolling**:
  - Perform short vs long two-finger swipes; verify proportional scrolling in the correct direction.
- [ ] **Virtual Keyboard Relay**:
  - Tap the keyboard icon in the toolbar, type into a text field; confirm keystrokes appear on the remote device.

---

## 4. Settings & Adaptive Bitrate
- [ ] **Sustained remote full-HD acceptance**:
  - Independently measure at least 12 Mbps on each controlled path. Capture
    ten minutes at landscape 1920×1080, 8 Mbps target and at least 30 decoded FPS.
  - Verify zero adaptive downgrades, no freeze over one second and less than 1%
    total freeze time. Export real measurement JSON and require assessor exit 0.
  - Repeat direct, TURN/UDP, UDP-blocked TURN/TCP and UDP-blocked TURN/TLS:443
    with IPv4, IPv6 and mixed-family relay reachability. Record selected protocol
    and family rather than inferring them from configured URLs.
  - Missing native longest-freeze telemetry produces inconclusive evidence;
    do not replace null observations with zeros.
- [ ] **Resolution Pinning (480p / 720p / 1080p / 1440p)**:
  - Open Settings modal, select 1080p or 480p; confirm resolution changes immediately.
- [ ] **Auto Adaptive Streaming**:
  - Select "Auto" quality tier; verify adaptive bitrate adjusts based on network conditions.
- [ ] **Congestion Step-Down (No Restart Storms)**:
  - Induce network congestion (e.g. enable device Network Link Conditioner); confirm quality steps down without dropping the connection or loop-restarting.
- [ ] **Tier-Switch Stability**:
  - Confirm switching quality tiers does not blank to the error overlay.

---

## 5. Instance Switching & Error Recovery
- [ ] Measure revocation stopping input/media within five seconds; retain the
  original 60-second service-outage grace. Exercise broker restart, PC offline,
  TURN unavailable, negotiation cancellation, network switch and LAN during outage.
- [ ] Run production 3600-second TURN credentials, renewal at 3300 seconds and
  rejection of new allocations using expired credentials; record elapsed times.
- [ ] **Quick-Switch Drawer**:
  - While streaming, open drawer (swipe from left or tap drawer icon) and select another instance.
  - **Pass condition**: Immediately switches to the new instance with keyframe prefetch (minimal buffering).
- [ ] **Server Kill & Reconnect**:
  - Stop the host server; confirm ErrorOverlay appears with "Can't reach server".
  - Restart host server, tap "Reconnect"; confirm stream re-establishes cleanly.
- [ ] **App Backgrounding & Foregrounding**:
  - Send app to background while streaming, wait 5 seconds, bring back to foreground.
  - **Pass condition**: Video and input DataChannel recover automatically without requiring manual reconnection.
