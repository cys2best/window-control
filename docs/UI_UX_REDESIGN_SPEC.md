# WindowControl (LD Control) — UI/UX Redesign Master Specification & AI Prompt

This document provides a comprehensive breakdown of the **WindowControl** application architecture, user personas, screen flows, detailed interactive functions, and a turnkey prompt template designed for AI UI generators (v0, Lovable, Claude, ChatGPT) and UI/UX designers in Figma.

---

## Part 1: Project & Product Context

### 1.1 Executive Summary
* **Product Name**: WindowControl (Client display: *LD Control* / *EmuCtrl*)
* **Core Value Proposition**: Ultra-low-latency remote streaming and touch control for Windows 11 applications and Android emulator instances (LDPlayer) streamed directly to mobile devices (iOS / Android) and modern browsers (PWA).
* **Target Audience**: Mobile gamers, multi-account MMO/gacha players, and PC automation power users who want to monitor, control, and play Android emulator instances running on their home PC from anywhere (LAN, Tailscale mesh, or mobile data via VPS relay) with gaming-grade responsiveness (<100ms video, <30ms input latency).
* **Current UI Philosophy**: Functional, warm-neutral minimalism (`#f2916f` coral accent, `#faf8f6` cards, `#1c1a19` ink text).
* **Primary Redesign Goal**: Elevate the UI/UX from an engineering prototype to a premium, dark-mode-first, gaming-grade remote play experience (inspired by Moonlight, Steam Link, PS Remote Play, and modern cyber-tactical design systems) optimized for high-density touch targets, thumb-zone ergonomics, and non-intrusive stream HUDs.

### 1.2 Technology & Architecture Context
* **Client Frontend**:
  * Unified codebase: React Native + `react-native-web` (`packages/ui`, `packages/core`).
  * Web / PWA: Next.js (`apps/web`, static export served by host or public tunnel).
  * Mobile: Expo / React Native (`apps/mobile`, targeted for iOS 15+ & Android).
* **Host PC Application**:
  * Backend: Python 3.11+ / FastAPI (`src/server/app.py`).
  * Video Capture: Scrcpy pipeline / C++ WebRTC Engine (`engine/`).
  * Desktop Host Shell: PyQt5 minimalist host status widget (`src/gui/launcher.py`) with Windows system tray minimization.
* **Networking & Streaming**:
  * Dual-path zero-config connection:
    1. *Local / Tailscale*: Direct WebRTC via WHEP protocol.
    2. *Remote WAN*: Cloud signaling bridge via VPS + Coturn TURN relay.
  * Adaptive bitrate streaming with manual resolution pinning (480p, 720p, 1080p, 1440p).
  * Binary & DataChannel input protocol: Touch events, multi-touch drag, wheel scroll, and soft keyboard relay.
* **Authentication & Ownership**:
  * Supabase Auth (Email + Password).
  * Trust-On-First-Use (TOFU) host claiming: The first authenticated account links to the PC host's hardware keypair, preventing unauthorized multi-tenant hijacking.

---

## Part 2: User Personas & Core Journeys

```mermaid
journey
    title User Journey: Remote Multi-Instance Gaming
    section Launch & Auth
      Open App / PWA: 5: User
      Authenticate via Supabase: 4: User
      Auto-claim host on first connect: 5: System
    section Instance Selection
      View active emulator instances: 5: User
      Check real-time preview thumbnail: 5: User
      Tap instance tile (Triggers instant IDR keyframe): 5: User
    section In-Stream Gaming
      Video stream renders full-screen (Landscape): 5: System
      Single-finger drag / tap remote control: 5: User
      Two-finger gesture to scroll emulator: 4: User
      Toggle virtual keyboard for in-game chat: 4: User
    section Switching & Controls
      Open quick-switch drawer or swipe toolbar: 5: User
      Switch to Instance #2 seamlessly: 5: System
      Tune stream quality / check latency HUD: 4: User
```

---

## Part 3: Detailed Pages & Screen Breakdown

### Screen 1: Auth & Onboarding (`Login.tsx`)
* **Purpose**: User sign-in / registration gatekeeper for secure remote access.
* **Device Orientation**: Portrait locked.
* **Key Components**:
  1. **Brand Identity Header**: App logo/icon, product title, and welcoming subtitle ("Control your PC Android instances from anywhere").
  2. **Auth Mode Toggle**: Clean segmented switch between "Sign In" and "Create Account".
  3. **Input Fields**:
     * Email field (with validation, auto-complete off).
     * Password field (with visibility toggle, secure text entry).
  4. **Primary Action Button**: High-contrast, stateful button ("Sign In" / "Create Account" / "Authenticating..." spinner).
  5. **Network / Host Status Badge**: Subtle indicator showing host endpoint reachability (Local Host vs VPS Tunnel).
  6. **Inline Error Banner**: Crisp warning pill displaying error details (e.g., "Invalid credentials", "Server unreachable", or "Install belongs to another owner").

---

### Screen 2: Windows / Instance Dashboard (`InstanceList.tsx`)
* **Purpose**: Primary hub displaying all online LDPlayer Android emulator instances and PC windows.
* **Device Orientation**: Portrait (adaptive for tablets / desktop web).
* **Key Components & Layout**:
  1. **Top App Bar**:
     * App branding with status dot.
     * Host Connection Card: Server IP / hostname, WebRTC ICE connectivity status chip (`Connected` / `Connecting` / `Disconnected`), and refresh button.
     * User profile avatar / Sign out action.
  2. **Instances Grid / List View**:
     * Header with instance counter (e.g., "4 instances running").
     * Pull-to-refresh (triggering backend ADB discovery).
  3. **Instance Card / Tile**:
     * **Live Screenshot Thumbnail**: 16:9 preview generated via ADB raw screencap, auto-refreshed.
     * **Status Badges**: `LIVE` badge for active stream, Resolution tag (e.g., `1920x1080`), and FPS indicator.
     * **Instance Title**: Emulator instance name (e.g., `LDPlayer-1 [Genshin Impact]`, `LDPlayer-2 [AFK Journey]`).
     * **Quick Actions**: Tap card to stream immediately. (Micro-interaction: `touchstart` initiates background IDR keyframe prefetch so stream paints instantly upon opening).
  4. **Empty State Card**:
     * Explanatory illustration when 0 instances are running: "No active LDPlayer instances detected. Launch an emulator on your PC, then pull down to refresh."
  5. **Bottom Navigation Bar**:
     * Floating capsule pill bar with tabs: `Instances` (active), `Stream` (quick jump to last active), and `Host Info`.

---

### Screen 3: Remote Stream & Control Canvas (`Stream.tsx`)
* **Purpose**: Core gaming experience. Edge-to-edge, ultra-low latency video stream and gesture relay.
* **Device Orientation**: **Landscape locked** (fullscreen, status bars & home indicators hidden/deferred).
* **Interactive Layers**:
  1. **Video Canvas Layer (Bottom)**:
     * High-performance WebRTC video renderer (`RTCPeerConnection` / WHEP).
     * Letterbox / pillarbox handling with zero stretching (maintains native emulator aspect ratio).
  2. **Touch & Gesture Capture Layer (Middle)**:
     * Fullscreen transparent gesture receptor.
     * **Single Touch / Drag**: Translated to left-click drag coordinates on PC host (`drag_start`, `drag_move`, `drag_end`).
     * **Two-Finger Vertical Scroll**: Smooth emulator scrolling (`scroll` with normalized dynamic delta).
     * Coalesced motion events (16ms throttle to prevent WebRTC DataChannel queue saturation).
  3. **Stream Floating Toolbar (Right Thumb Zone)**:
     * Sleek, semi-transparent vertical dock pinned to the right edge (or collapsible into a floating orb).
     * **Connection Dot & Badge**: Real-time status (`LIVE` green, `SYNC` amber, `DOWN` red).
     * **Instance Switcher Action**: Opens slide-out switcher drawer.
     * **Soft Keyboard Action**: Toggles hidden input relay to bring up the iOS/Android virtual keyboard.
     * **Quality Settings Action**: Opens Stream Settings modal.
     * **Live Stats Action**: Toggles real-time HUD stats overlay.
     * **Exit / Back Action**: Returns to Instance Dashboard (smoothly restores portrait orientation).
     * **Swipe Gesture on Toolbar**: Vertical swipe up/down on the toolbar switches directly to the next/previous instance without opening the drawer.
  4. **Hidden Keyboard Input Proxy**:
     * Invisible `TextInput` relay capturing native keyboard keystrokes and translating key names (e.g., `Enter` -> `Return`, `Backspace` -> `BackSpace`) to the server.

---

### Screen 4: Slide-Out Instance Switcher Drawer (`SwitchDrawer.tsx`)
* **Purpose**: Instant switching between game instances without stopping the stream or returning to the dashboard.
* **Presentation**: Frosted glass drawer sliding in from the left or bottom edge over the live stream.
* **Key Components**:
  * Header with close button and total instance count.
  * Scrollable list of compact instance cards:
    * Thumbnail preview.
    * Instance name & resolution.
    * Active indicator (`LIVE` glowing tag).
  * Single-tap switch: Triggers instantaneous stream transition with client-side session reuse (stale stream stays visible until new video frames arrive, eliminating black flashes).

---

### Screen 5: Stream Settings & Tuning Modal (`SettingsModal.tsx`)
* **Purpose**: Video quality tuning and overlay preferences.
* **Presentation**: Centered modal with glassmorphic backdrop.
* **Key Components**:
  * **Quality Tier Selector**: Segmented horizontal pill buttons:
    * `Auto` (Adaptive bitrate based on RTT & packet loss)
    * `480p` (Low bandwidth / Mobile data saver)
    * `720p` (Balanced performance)
    * `1080p` (High fidelity)
    * `1440p` (Ultra high-res PC gaming)
  * **Overlay Preferences**:
    * Toggle switch for "Show Live Diagnostics HUD".
    * Toggle for "Touch Haptic Feedback".
    * Toggle for "Gamepad Button Overlay" (virtual on-screen buttons).
  * "Done" confirm button.

---

### Screen 6: Live Performance Stats HUD (`StatsOverlay.tsx`)
* **Purpose**: Telemetry overlay for competitive players and network diagnostics.
* **Presentation**: Minimalist floating dark HUD pinned to the top-left corner with mono typography.
* **Metrics Displayed**:
  * Active Quality Tier & Target Resolution (e.g., `TIER: 1080p @ 60fps`)
  * Input Round-Trip Time (e.g., `INPUT RTT: 18ms`)
  * Video Latency / Decode Delay (e.g., `VIDEO: 42ms`)
  * Transport Mode: `P2P (LAN/Tailscale)` vs `RELAY (TURN/VPS)`

---

### Screen 7: Stream Reconnect & Error Overlay (`ErrorOverlay.tsx`)
* **Purpose**: Graceful recovery when network drops or PC goes to sleep.
* **Presentation**: Darkened modal over frozen canvas with animated pulse indicator.
* **Key Components**:
  * Clear diagnostic title: "Connection Interrupted" or "Host Sleeping".
  * Helpful troubleshooting tips: "Verifying local network and relay pathways..."
  * Primary Action: "Reconnect Now" (with retry countdown/spinner).
  * Secondary Action: "Back to Dashboard".

---

### Screen 8: Desktop Host Monitor Widget (`LauncherWindow` / `launcher.py`)
* **Platform**: Windows 11 Desktop (400×460px compact utility window).
* **Key Components**:
  1. **Header**: Host status dot (Green pulsing "Server Running on :8889"), Version tag, and System Tray minimize button.
  2. **Host Status Cards**:
     * **Claimed Account**: Supabase user email or "Unclaimed (Open to link)".
     * **Network Endpoints**: Detected Local IP (e.g., `192.168.1.100`), Tailscale IP (e.g., `100.x.y.z`), and port.
     * **VPS Relay Connectivity**: Status of cloud signaling bridge and public tunnel.
     * **Active Streams Counter**: Number of connected viewers (e.g., `1 active stream`).
  3. **Auto-Updater Card**: Notice when a new client/server version is available with a 1-click "Install Update" button.
  4. **System Tray Integration**: Background running with quick-access tray menu (Open Host Monitor, Restart Service, Stop Server).

---

## Part 4: Technical & UX Constraints for Design

| Feature / Area | UX Requirement / Constraint | Technical Reason |
| :--- | :--- | :--- |
| **Stream Aspect Ratio** | Strict preservation of instance resolution (never stretch). | Emulator resolutions vary (16:9, 18:9, 4:3). Coordinate normalization maps touch pixels to exact emulator pixels. |
| **Mobile Thumb Zones** | Controls must sit in the natural thumb arcs on both sides. | Users hold phones with two hands in landscape; center touches are reserved for game interaction. |
| **System Gestures** | Hide home indicator and defer edge swipes (`preferredScreenEdgesDeferringSystemGestures`). | Accidental edge swipes must not close the game or trigger iOS app switching during combat. |
| **Micro-Interactions** | Haptic pulse on button presses; zero delayed clicks (no 300ms mobile tap delay). | Gaming requires immediate tactile acknowledgment. |
| **Switch Latency** | Pre-fetch keyframe on touchstart; retain previous frame until new frame arrives. | Avoids jarring black-screen flashes when switching between 5 emulator instances. |
| **Theme & Contrast** | Deep dark canvas (`#0d0f12`) with high-visibility neon/accent accents (`#38bdf8` cyan or `#ff6b4a` coral). | Ambient glare reduction during gaming; clear HUD readability over complex game scenes. |

---

## Part 5: Ready-to-Use UI/UX Redesign Prompt

> **Instructions for Use**: Copy the prompt block below directly into **Claude**, **ChatGPT**, **v0.dev**, **Lovable**, or give it to a product designer to generate design concepts, Figma components, or React code.

```markdown
You are an elite Lead Product Designer and Design Systems Architect specializing in gaming interfaces, remote play applications (e.g., Steam Link, PS Remote Play, Moonlight, GeForce NOW), and high-performance developer tools.

### Project Overview
I need you to redesign the UI/UX for "WindowControl" (LD Control) — a cross-platform remote streaming application that allows users to stream and control multiple Android emulator (LDPlayer) instances running on a Windows 11 PC from an iPhone (iOS), tablet, or web browser with ultra-low latency (<30ms input, <100ms video).

### Core Problem with Current UI
The current UI uses a generic, light-neutral palette (coral #f2916f and warm paper #faf8f6) with basic form inputs. It feels like an administrative dashboard rather than an immersive, ergonomic gaming tool. The stream overlay toolbar is rigid, and multi-instance management lacks visual hierarchy and game-centric Polish.

### Design System Direction & Aesthetics
1. Theme: Cyber-Tactical Dark Mode (OLED-optimized blacks #090a0f, deep slate cards #13161f, border highlights #222738).
2. Accent Colors: Electric Cyan (#00E5FF) for primary actions/connected states, Vivid Tangerine (#FF5722) for recording/live streaming badges, and Mint Green (#10B981) for low-latency telemetry.
3. Typography: Modern technical sans-serif (e.g., Space Grotesk / Inter) paired with a clean monospace font (e.g., JetBrains Mono) for latency HUD and telemetry.
4. Ergonomics: Built for landscape thumb-zones on mobile. Floating controls must collapse into a minimal, non-distracting pill or edge dock that doesn't obstruct in-game UI.

### Screens to Design
Please provide detailed UI layouts, visual hierarchy, micro-interactions, and component specifications for the following 4 primary screens and 3 modal overlays:

1. Screen 1: Auth & Host Onboarding (Mobile Portrait & Web)
   - Mode switch: "Sign In" vs "Create Account".
   - Clean inputs with focus states and password reveal.
   - Host connectivity indicator (LAN vs Cloud Relay status).
   - "Claim Host PC" security notice.

2. Screen 2: Windows / Instance Control Dashboard (Mobile Portrait & Tablet/Web Grid)
   - Top Bar: Host server connection card with live latency ping, active IP, and refresh button.
   - Grid of Emulator Cards:
     - 16:9 thumbnail preview with live screenshot.
     - Badges: Active 'LIVE' glow, resolution (e.g. 1080p), FPS, and game title tag.
     - Single-tap to launch stream (instant IDR prefetch).
   - Floating Bottom Capsule Bar: Instances, Stream Quick-Jump, and Server Health.

3. Screen 3: Immersive Stream & Remote Play Screen (Mobile Landscape - Fullscreen)
   - Edge-to-edge video canvas with letterbox protection.
   - Collapsible Thumb-Dock Toolbar on the right edge:
     - Status dot (LIVE/SYNC/DOWN with ms ping).
     - Quick-switch instances button (with vertical swipe shortcut).
     - Virtual keyboard trigger.
     - Stream settings trigger.
     - Diagnostic HUD toggle.
     - Back to dashboard button.
   - Minimalist On-Screen HUD: Floating top-left pill displaying [1080p | 60 FPS | RTT 16ms | P2P Direct].

4. Screen 4: Quick-Switch Drawer (In-Stream Overlay)
   - Slide-in glassmorphic side drawer or horizontal bottom carousel.
   - Compact live cards of other instances to hot-swap without dropping the stream.

5. Screen 5: Stream Quality & Settings Modal
   - Quality Tier Selector: Segmented pill controls [Auto | 480p | 720p | 1080p | 1440p].
   - Diagnostic toggles (Show HUD, Touch Haptics, Virtual Gamepad buttons).

6. Screen 6: Windows 11 Host Desktop Monitor Widget (400x460px Desktop App)
   - Minimalist cyberpunk widget matching the Windows 11 Mica/Acrylic aesthetic.
   - Server status (Running port 8889), detected Tailscale/LAN IPs, relay status, connected client counter, and system tray minimization.

### Deliverables Expected
- Layout structure & visual hierarchy for each screen.
- Exact styling specifications (colors, shadows, border radii, blur/glassmorphism).
- Interaction design & animation states (transitions, swipe gestures, hover/press states).
- Production-ready React / Tailwind CSS / React Native code snippet for the Stream Screen with its collapsible thumb toolbar.
```
