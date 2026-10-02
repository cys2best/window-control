# Mobile Device Smoke Test Checklist (v3.1.0)

Run this checklist on a physical iOS or Android device (via Expo Go or dev build) connected to the WindowControl host on LAN or over Tailscale.

---

## 1. Zero-Config Launch & Authentication
- [ ] **Direct Launch without Manual URL**:
  - Launch the mobile app from a clean state (or after clearing app storage).
  - **Pass condition**: App opens directly to the **Login** screen without prompting for a manual "Server base URL".
- [ ] **Automatic API Base Connection**:
  - Enter valid account credentials and tap Sign In.
  - **Pass condition**: Authenticates seamlessly using the configured default tunnel (`EXPO_PUBLIC_API_URL`) and transitions to the **InstanceList** screen.
- [ ] **Persistent Session on Relaunch**:
  - Force quit the app and reopen it.
  - **Pass condition**: Bypasses Login and navigates directly to the **InstanceList** screen.

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
  - **Pass condition**: Dual-transport manager races local WHEP against VPS relay; video paints quickly, toolbar network dot turns green, and stats overlay shows active streaming.
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
- [ ] **Quick-Switch Drawer**:
  - While streaming, open drawer (swipe from left or tap drawer icon) and select another instance.
  - **Pass condition**: Immediately switches to the new instance with keyframe prefetch (minimal buffering).
- [ ] **Server Kill & Reconnect**:
  - Stop the host server; confirm ErrorOverlay appears with "Can't reach server".
  - Restart host server, tap "Reconnect"; confirm stream re-establishes cleanly.
- [ ] **App Backgrounding & Foregrounding**:
  - Send app to background while streaming, wait 5 seconds, bring back to foreground.
  - **Pass condition**: Video and input DataChannel recover automatically without requiring manual reconnection.
