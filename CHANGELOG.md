# Changelog

All notable changes to this app are recorded here.

---

## [v3.1.0] — September 06, 2026

This release introduces zero-config auto-discovery and dual-transport WebRTC streaming, removes manual server configuration screens across web and mobile clients, and transitions the Windows host desktop interface into a lightweight status monitor widget.

### What's New
- **Zero-Config Auto-Discovery & Dual-Transport Streaming**: Video sessions now race direct local LAN connections against the public VPS relay simultaneously. Native WebRTC ICE candidate discovery automatically selects the fastest available peer-to-peer route on LAN/Tailscale without manual network configuration or custom UDP daemons, while seamlessly falling back to TURN relay when connecting off-LAN.
- **Direct-to-App Workflow**: Eliminated the manual Server base URL configuration screen (`ServerSetup`) across both web and mobile apps. Clients connect seamlessly using environment defaults and secure session storage, routing directly to Login or Instance List.
- **Minimal Host Monitor Widget (Option B)**: Redesigned the Windows desktop application into a compact ~400px host status monitor. Retired `pywebview`, `DesktopWindow`, and internal subprocess shells in favor of a clean native PyQt widget displaying server health, local/Tailscale IP endpoints, public relay status, and active stream count with minimize-to-tray background management.

### Improvements
- Streamlined mobile and web application navigation flows by deleting redundant server setup screens and dead legacy routes.
- Robust WebSocket signaling viewer connection logic with immediate offer dispatch if the signaling socket is already open.
- Trailing slashes on configured server environment base URLs are automatically sanitized and normalized.

---

## [v3.0.0] — September 05, 2026

This release replaces the old streaming pipeline with a new native engine, moves everyone onto real account-based login, and unifies the web, mobile, and desktop apps onto one shared codebase.

### What's New
- Real accounts: you now register and log in with your own email/password instead of a shared access code, on web, mobile, and the desktop launcher alike.
- The desktop tray now has an "Open App" button that opens the same app the browser and phone use, right on your PC, instead of pointing you to open a browser yourself.
- The web app can now be added to your phone's home screen as its own installable app, with its own icon.

### Improvements
- Video streaming now runs on a rebuilt native engine, which switches between windows faster and recovers on its own if the phone's screen-capture connection briefly drops.
- The web, mobile, and desktop apps now share the same underlying code for logging in, listing your devices, and watching a stream — fixes and improvements to one automatically reach all three going forward.
- The very first account to log into a PC becomes its permanent owner. A different account can no longer see or use that PC's devices just by logging in, closing a real security gap in the old shared-code system.
- Switching between windows or reconnecting after a brief network hiccup is smoother and less likely to require a manual retry.

### Fixed
- An issue where a different person's account could quietly take over a PC that already belonged to someone else has been closed — ownership is now locked to the first account that claims a PC.
- The public/remote streaming connection now always requires your own real login — a copyable shared code can no longer be used to connect to someone else's stream.

### Important Changes
- If you were relying on the old shared access-code login, that no longer works — everyone needs to register or log in with a real account going forward.
- The old browser-based web client has been fully retired and replaced. If you had it bookmarked with any old settings saved, you'll be asked to reconfigure once after updating.

---
