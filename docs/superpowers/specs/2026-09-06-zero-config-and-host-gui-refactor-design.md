# Zero-Config Discovery & Host GUI Refactor — Design

## Problem

1. **Manual Server URL Entry in Clients**: The mobile and web clients currently present a manual `ServerSetup` screen requiring the user to type a full host URL (e.g. `http://100.77.31.86:8080`). This is cumbersome and unnecessary because the project already operates a public VPS HTTP tunnel (`infra/vps/tunnel` / `PUBLIC_UI_URL`), WebRTC signaling bridge (`infra/vps/signaling` / `VPS_SIGNALING_URL`), and coturn STUN/TURN relay (`infra/vps/coturn`).
2. **WebRTC Dual-Transport Gap in `@wc/core`**: Prior to the unified frontend cutover (commit `4437d8c`), `src/client/engine_session.js` supported a dual-transport race: starting local WHEP and public WebSocket signaling concurrently, allowing WebRTC ICE to automatically choose the fastest path (direct LAN IP when local, TURN relay when remote). The initial cutover only ported local WHEP into `@wc/core/src/webrtc/whep.ts`, leaving public signaling as a gap.
3. **Unnecessary Embedded Desktop Client**: The Windows desktop application (`src/gui/launcher.py` and `apps/desktop/`) includes a `DesktopWindow` spawning a `pywebview` child process to render the web client locally on the host machine, along with an "Open App" button and a static QR code. The host PC is solely the streaming server and manager; opening an embedded client to view its own stream adds unnecessary subprocess complexity and the heavy `pywebview` dependency.

## Goal

1. **Zero-Config Client Experience**: Completely eliminate manual server IP/URL entry. The client defaults its API calls to the public VPS HTTP tunnel, logs in via Supabase, and uses WebRTC ICE to automatically discover and connect to direct LAN IP when on the same network or route through the VPS relay when away.
2. **Dual-Transport WebRTC Session in `@wc/core`**: Reintroduce the concurrent dual-transport race (local WHEP + public WebSocket signaling with STUN/TURN ICE) in `@wc/core`, giving both mobile and web clients automatic LAN/remote failover.
3. **Streamlined Windows Host GUI (Option B Minimal Host Monitor)**: Retire `pywebview`, `DesktopWindow`, and `apps/desktop/webview_main.py`. Redesign the Windows desktop application into a compact ~400px host monitor showing server status, Supabase account, detected network IPs, relay status, and active client count, with clean system tray minimization.

## Non-goals

- `infra/vps/` (tunnel, signaling, coturn): No changes to existing VPS services; they already provide full HTTP tunnel and WebRTC signaling capabilities.
- `engine/` C++ WebRTC Engine: Unchanged; it already supports local WHEP and public signaling registration.
- Custom UDP broadcast / mDNS daemon: Unnecessary; WebRTC ICE natively handles local candidate vs. relay candidate discovery.

## Architecture

### 1. Windows Desktop Host Refactor (PyQt5)

- **Retire pywebview**:
  - Delete `apps/desktop/window.py` (`DesktopWindow`) and `apps/desktop/webview_main.py`.
  - Remove `pywebview` from `pyproject.toml`.
  - In `src/main.py`, remove the desktop window creation, child process management, and `--webview-window` relaunch handling.
- **Redesign LauncherWindow (`src/gui/launcher.py`) — Option B Minimal Host Monitor**:
  - Dimensions: Fixed compact card (~400px width, ~480px height).
  - Header: Application title `WindowControl Host`, version badge, running status indicator (`Server Running: :8080`).
  - Status Details Card:
    - **Account**: Logged-in Supabase user email (or unauthenticated status if auth disabled).
    - **Detected Network**: Local LAN IP (e.g. `192.168.1.150:8080`) and Tailscale IP (if active).
    - **VPS Relay**: Connection health to `VPS_SIGNALING_URL` (`Connected` / `Offline`).
    - **Active Streams**: Real-time connected client viewer count (`Idle` or `N clients streaming`).
  - Actions:
    - "Minimize to Tray" (hides window to system tray; tray default double-click / Show restores it).
    - "Sign Out" / "Stop Server".
  - Removed elements: The "Open App" button and the large QR code box are completely removed.
- **Tray Icon (`apps/desktop/tray.py`)**:
  - Retains existing clean menu: `Show`, `Stop Server`, separator, `Reinstall / Update` (if updater configured), `Exit`.

### 2. Client Zero-Config Flow (`@wc/ui`, `apps/mobile`, `apps/web`)

- **Remove Manual Setup**:
  - Delete `packages/ui/src/screens/ServerSetup.tsx` and its test file `packages/ui/src/screens/ServerSetup.test.tsx`.
  - Remove `ServerSetup` export from `packages/ui/src/index.ts`.
  - In `apps/mobile/src/navigation/Root.tsx`:
    - Remove `ServerSetup` from the stack.
    - Landing logic: If unauthenticated (`!authToken`) -> `Login`; If authenticated -> `InstanceList`.
  - In `apps/web`:
    - Delete `apps/web/src/app/setup/page.tsx`.
    - Update `apps/web/src/app/page.tsx`: redirect directly to `/login` (if `!authToken`) or `/instances` (if authenticated).
- **Default API Endpoint**:
  - In `packages/core/src/api/ServerContext.tsx`, initialize the default base URL from environment configuration (`EXPO_PUBLIC_API_URL` on mobile, or `window.location.origin` on web).
  - The client interacts with the PC FastAPI server transparently via the VPS HTTP tunnel.

### 3. Dual-Transport WebRTC Engine Session in `@wc/core`

- **Signaling WebSocket Client (`packages/core/src/webrtc/signaling.ts`)**:
  - Implements the client side of `infra/vps/signaling/server.js`:
    - Connects to `${signaling_url}?session=${public_session}&role=viewer&token=${authToken}`.
    - Sends client SDP offer over WebSocket.
    - Receives SDP answer from engine.
- **Dual-Transport Session Manager (`packages/core/src/webrtc/session.ts`)**:
  - Reworks stream connection from single-WHEP into a concurrent dual-race matching `engine_session.js`:
    - When `select(serial)` returns:
      - `whep_url`, `whep_token` (local WHEP)
      - `signaling_url`, `public_session` (public signaling)
      - `ice_servers` (STUN + TURN)
    - Concurrently launches `startLocal(WHEP)` (if configured) and `startPublic(Signaling WebSocket)` (if configured).
    - WebRTC ICE gathers both host (local LAN IP) and relay candidates.
    - Whichever transport establishes first (connected ICE + open data channel + video track) is adopted as the active session.
    - The non-winning attempt is cleanly aborted.
    - On LAN: local connection connects in ~100–200ms.
    - Remote / 5G: public signaling connects seamlessly through coturn TURN.
  - Updates `packages/ui/src/screens/Stream.tsx` to use the dual-transport session manager instead of direct `connectWhep`.

## Security & Authentication

- Supabase JWT authentication is preserved end-to-end:
  - All API requests carry `Authorization: Bearer <token>`.
  - VPS Signaling validates viewer token with Supabase JWKS before bridging to engine.
  - Host install ownership check ensures viewers only connect to their own claimed installs.
- Removing `pywebview` eliminates a local desktop attack surface and removes inter-process webview communication.

## Testing & Verification

1. **Desktop Host (Python)**:
   - Run `uv run pytest tests/ apps/desktop/ -v`.
   - Verify `LauncherWindow` constructs and renders Option B layout without `on_open_app`.
   - Verify tray icon actions (`Show`, `Stop Server`, `Exit`).
   - Verify clean process startup with `src/main.py` without `pywebview`.
2. **Core Logic (Jest)**:
   - Run `npm run test:core`:
     - Test `signaling.ts`: WebSocket handshake, SDP offer/answer exchange, error handling.
     - Test `session.ts`: Dual-race winner adoption, cancellation of slower transport, fallback when local WHEP fails.
3. **UI & Mobile (Jest)**:
   - Run `npm run test:ui`:
     - Test `RootNavigator` and screen transitions (`Login` -> `InstanceList` -> `Stream`).
     - Verify removal of `ServerSetup`.
4. **Web App (Jest)**:
   - Run `npm test -w apps/web`:
     - Verify root redirect to `/login` or `/instances`.
     - Verify build succeeds with `npm run build -w apps/web`.

## Migration & Rollout

- Single cohesive change across the monorepo:
  1. Remove pywebview and refactor `src/gui/launcher.py` into Option B.
  2. Implement dual-transport WebRTC manager in `packages/core`.
  3. Update `packages/ui` screens and navigation to eliminate `ServerSetup`.
  4. Run full Python and Jest test suites.

## Technical Debt & Future Work

- **Multi-Tenant VPS HTTP Tunnel Routing**:
  - *Current State*: `infra/vps/tunnel/server.js` currently maintains a single active host PC connection (`let pcConn = null; ws.close(1008, 'a PC is already registered')`). While unauthorized cross-user access to a connected PC is strictly blocked by FastAPI's `_auth_gate` (403 Forbidden) and WebRTC stream sessions are isolated by Supabase account-signed Ed25519 tokens (`${owner_user_id}.${instance}`), only one host PC can register with the tunnel server at a time across the entire VPS.
  - *Future Plan*: Upgrade `infra/vps/tunnel/server.js` to support multi-tenant dispatching (`Map<userId, pcWebSocket>`). The tunnel server will extract `userId` from the incoming request's Supabase JWT `Authorization: Bearer <token>` and route HTTP calls exclusively to the matching user's registered host PC.

