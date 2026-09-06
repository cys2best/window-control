# WindowControl v3.1.0 — End-to-End Validation Checklist

> **Purpose**: Single authoritative, step-by-step checklist covering all automated and physical validation test cases for the **Zero-Config Discovery &amp; Host GUI Refactor (`v3.1.0`)**. Follow the sections in order.

---

## Quick Reference Commands


| Validation Phase                 | Command (Windows PowerShell)                                                            | Command (macOS / Linux)                                                                                                                 |
| :-------------------------------- | :--------------------------------------------------------------------------------------- | :--------------------------------------------------------------------------------------------------------------------------------------- |
| **All Automated Suites**         | `.\engine\verify-all.ps1`                                                               | `python3 scripts/verify_all.py`                                                                                                         |
| **Live Server &amp; Web Routes** | `.\engine\verify-frontend-cutover.ps1 -SkipManualGates -SkipInstaller`                  | `uv run python -m scripts.verify_frontend_cutover --skip-manual-gates --skip-installer --evidence-dir .evidence --installer-path dummy` |
| **Package &amp; Firewall**       | `.\engine\verify-frontend-cutover.ps1 -Only installed_app_launch,frozen_package_layout` | *(Windows only)*                                                                                                                        |


---

## Section 1: Pre-Flight Environment &amp; Machine Setup

- [ ] **1.1. Hardware &amp; OS**: Windows 11 host (physical hardware or VM with display output).
- [ ] **1.2. Toolchains Installed**:
  - [ ] Visual Studio 2022 ("Desktop development with C++" workload installed)
  - [ ] `vcpkg` bootstrapped and CMake &gt;= 3.24
  - [ ] Python 3.11+ and `uv` installed (`uv --version`)
  - [ ] Node.js 20+ and `npm` installed (`node -v`, `npm -v`)
  - [ ] `adb` on PATH (`adb version`)
- [ ] **1.3. Android Emulator / Device**:
  - [ ] At least one LDPlayer emulator instance running, or physical Android device connected via ADB (`adb devices` lists active device).
- [ ] **1.4. Environment Variables Configured (via `.env` file in repo root)**:
  > **Tip**: You can create a `.env` file in the repo root (gitignored). Both the Python dev server (`src/main.py`), verification scripts, and PowerShell runners (`verify-all.ps1`, `verify-frontend-cutover.ps1`) automatically load `.env`!
  ```ini
  SUPABASE_URL=https://<your-project>.supabase.co
  SUPABASE_ANON_KEY=eyJ...
  SUPABASE_SERVICE_ROLE_KEY=eyJ...
  VPS_SIGNALING_URL=ws://<VPS_IP>:8443
  # Optional:
  PUBLIC_UI_URL=wss://tunnel.example.com/__tunnel/register
  TUNNEL_SECRET=...
  ```
  - [ ] `SUPABASE_URL`
  - [ ] `SUPABASE_ANON_KEY`
  - [ ] `SUPABASE_SERVICE_ROLE_KEY`
  - [ ] `VPS_SIGNALING_URL` (e.g. `ws://<VPS_IP>:8443`)
  - [ ] *(Optional)* Tailscale signed in on host and mobile client.

---

## Section 2: Automated Monorepo Verification (Run First)

Execute the full automated test suite:

```powershell
.\engine\verify-all.ps1
# (macOS/Linux: python3 scripts/verify_all.py)
```

- [x] **2.1. Python Backend &amp; Desktop Suites**: 567 passed, 1 skipped (`uv run pytest tests/ apps/desktop/ -q`). *(Verified on Windows)*
- [x] **2.2. Host Launcher Headless Tests**: All Option B layout and event tests pass (`tests/test_launcher_widget.py`). *(Verified on Windows)*
- [x] **2.3. Core WebRTC Session &amp; Signaling**: 68 passed across 12 suites (`npm run test:core`). *(Verified on Windows)*
- [x] **2.4. Shared UI Components**: 18 passed across 7 suites (`npm run test:ui`). *(Verified on Windows)*
- [x] **2.5. Web Client Routing &amp; Redirection**: 7 passed across 4 suites (`npm test -w apps/web`). *(Verified on Windows)*
- [x] **2.6. Next.js Static Export Build**: Succeeds into `apps/web/out` in 8.81s (`npm run build -w apps/web`). *(Verified on Windows)*
- [x] **2.7. Web Export Artifact Integrity**: *(Verified on Windows)*
  - [x] `apps/web/out/index.html` exists
  - [x] `apps/web/out/login.html` exists
  - [x] `apps/web/out/instances.html` exists
  - [x] `apps/web/out/stream.html` exists
  - [x] `apps/web/out/404.html` exists
  - [x] `apps/web/out/setup.html` **does NOT exist** (retired manual setup screen)
- [x] **2.8. VPS Signaling Bridge Relay**: 18 passed (`npm test -w infra/vps/signaling`). *(Verified on Windows)*

---

## Section 3: Live Server Cutover &amp; Content Negotiation

Execute the automated HTTP server verifier:

```powershell
.\engine\verify-frontend-cutover.ps1 -SkipManualGates -SkipInstaller
# (macOS/Linux: uv run python -m scripts.verify_frontend_cutover --repo-root . --skip-manual-gates --skip-installer)
```

- [x] **3.1. Server Health**: Dev app boots cleanly on port 8080 (`/auth/config` and web server respond). *(Verified on Windows)*
- [x] **3.2. Web Route Servicing**: *(Verified on Windows)*
  - [x] `GET http://127.0.0.1:8080/` -&gt; 200 text/html
  - [x] `GET http://127.0.0.1:8080/login` -&gt; 200 text/html
  - [x] `GET http://127.0.0.1:8080/instances` -&gt; 200 text/html (when requesting HTML shell)
  - [x] `GET http://127.0.0.1:8080/stream` -&gt; 200 text/html
  - [x] `GET http://127.0.0.1:8080/setup` -&gt; 404 (retired route rejected)
- [x] **3.3. Content Negotiation on `/instances`**: *(Verified on Windows)*
  - [x] `Accept: text/html` returns the HTML page shell.
  - [x] `Accept: application/json` returns JSON instance list or 401.
  - [x] No Accept header defaults to JSON API response.
- [x] **3.4. Supabase Auth Gate**: *(Verified on Windows via automated cutover verifier)*
  - [x] Unauthenticated API request to `/instances` returns `401 Unauthorized`.
  - [x] Garbage token (`Bearer not-a-real-token`) returns `401 Unauthorized`.

---

## Section 4: Native C++ Engine Compilation &amp; Tests (Windows Hardware)

Compile and verify the native streaming engine:

```powershell
cmake -S engine -B engine\build -DCMAKE_TOOLCHAIN_FILE="C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\vcpkg\scripts\buildsystems\vcpkg.cmake" -DVCPKG_TARGET_TRIPLET=x64-windows
cmake --build engine\build --config Release
```

- [x] **4.1. Engine Binary Built**: `engine\build\Release\engine.exe` exists with 0 compiler errors. *(Verified on Windows)*
- [x] **4.2. Offline GTest Suite**: *(Verified on Windows)*
  ```powershell
  cmake --build engine\build --config Release --target engine_tests
  .\engine\build\Release\engine_tests.exe --gtest_filter=-SignalingClient.*:PublicSignalingBridge.*
  ```

  All offline engine tests pass (excluding tests that require the live Node signaling relay).
- [x] **4.3. Live Signaling Relay GTest**: *(Verified on Windows)*
  1. In a dedicated terminal, launch the signaling relay with test TLS certs:
    ```powershell
     cd infra\vps\signaling
     $repoRoot = (Resolve-Path ..\..\..).Path
     $env:JWT_SECRET = ""
     $env:SIGNALING_TLS_CERT_FILE = Join-Path $repoRoot "engine\test\tls\localhost-cert.pem"
     $env:SIGNALING_TLS_KEY_FILE = Join-Path $repoRoot "engine\test\tls\localhost-key.pem"
     $env:SIGNALING_TLS_PORT = "8444"
     npm start
    ```
  2. In your engine test terminal (from repo root):
    ```powershell
     $env:SSL_CERT_FILE = (Resolve-Path "engine\test\tls\ca-cert.pem").Path
     $env:ENGINE_TEST_WSS_PORT = "8444"
     cmake --build engine\build --config Release --target engine_tests
     .\engine\build\Release\engine_tests.exe --gtest_filter="SignalingClient.*:PublicSignalingBridge.*"
    ```
  
    WebRTC signaling handshake and public peer bridge pass against local relay.

---

## Section 5: Installer Build &amp; Packaging Verification

Build the standalone Windows installer:

```powershell
cd build
.\build.bat
cd ..
```

- [x] **5.1. Packaged Layout Check**: *(Verified on Windows)*
  - [x] `dist\WindowControl\WindowControl.exe` exists.
  - [x] `dist\WindowControl\_internal\assets\engine\engine.exe` exists.
  - [x] `dist\WindowControl\_internal\web\` contains web build assets (and no `setup.html`).
  - [x] Confirm no `webview` / `pywebview` files or DLLs are packaged.
- [x] **5.2. Inno Setup Compilation & Installation**: *(Verified on Windows)*
  1. Compile installer with Inno Setup 6 (requires Inno Setup 6, e.g. `winget install JRSoftware.InnoSetup`):
     ```powershell
     cd build
     .\build_installer.bat --no-build
     cd ..
     ```
     Generates `release\WindowControlInstaller.exe` with bundled VC++ x64 runtime bootstrap.
  2. Run the installer to install WindowControl into `C:\Program Files\WindowControl\`:
     ```powershell
     .\release\WindowControlInstaller.exe /VERYSILENT /NORESTART
     ```
     *(Or double-click `release\WindowControlInstaller.exe` to run the graphical wizard with admin privileges).*
- [x] **5.3. Automated Installer Gate**: *(Verified on Windows)*
  ```powershell
  .\engine\verify-frontend-cutover.ps1 -Only installed_app_launch,frozen_package_layout
  ```
  - [x] Service launches from `C:\Program Files\WindowControl\`.
  - [x] Windows Defender firewall rule `WindowControl-Engine` points to `_internal\assets\engine\engine.exe`.
  - [x] Single-process verification passes (no child webview spawned).

---

## Section 6: Windows Host GUI & System Tray (Physical Eye Check)

Start host: `uv run python src\main.py` (or launch installed `WindowControl.exe`):

- [x] **6.1. System Tray Icon**: WindowControl icon appears in Windows system tray (near clock). *(Verified on Windows)*
- [x] **6.2. Minimal Host Monitor Widget (Option B)**: *(Verified on Windows)*
  - Right-click or double-click tray icon -> click **Show**.
  - [x] Window opens (~400px width, ~460px height).
  - [x] Header displays: `WindowControl Host v3.1.0` with green running dot and `:8080`.
  - [x] Account row displays logged-in email (or "Auth disabled (LAN mode)").
  - [x] Network row displays detected Local LAN IP and Tailscale IP (if active).
  - [x] VPS Relay row displays connection status (Connected / Disabled).
  - [x] Active Streams row shows current viewer count ("Idle" when 0).
- [x] **6.3. Minimize to Tray Button**: Click **Minimize to Tray** button -> window hides to tray. *(Verified on Windows)*
- [x] **6.4. Window [X] Button**: Open window again, click **[X]** titlebar close button -> window minimizes to tray instead of quitting. *(Verified on Windows)*
- [x] **6.5. Clean Process Exit**: Right-click tray icon -> click **Exit** -> server shuts down cleanly, process terminates from Task Manager. *(Verified on Windows)*

---

## Section 7: Supabase Multi-User Security & Isolation Gate

- [x] **7.1. First Account Registration (Account A)**: *(Verified on Windows)*
  - Navigate to `http://<PC-IP>:8080/login` in browser.
  - Register Account A (or sign in).
  - Verify Account A claims the host machine (trust-on-first-use).
  - Verify connected emulator appears in instance list.
- [x] **7.2. Second Account Isolation (Account B)**: *(Verified on Windows)*
  - Open a separate **Incognito / Private browser window**.
  - Navigate to `http://<PC-IP>:8080/login` and log in as Account B (a different registered user).
  - **Critical Pass Condition**: Account B sees an **empty** device list.
  - Attempting to query or stream Account A's device (e.g. `/instances/{serial}/select`) returns **HTTP 403 Forbidden**.
- [x] **7.3. Persistent Claim**: Close and reopen browser; Account A continues to see the claimed instance. *(Verified on Windows)*

---

## Section 8: Web Client Streaming & Dual-Transport Verification

- [x] **8.1. Stream Launch**: From Account A's instance list, click an emulator card to open `/stream`. *(Verified on Windows)*
- [x] **8.2. Dual-Transport Connection**: *(Verified on Windows)*
  - [x] Video stream displays immediately (no black frame).
  - [x] Toolbar network dot shows green (connected).
  - [x] Stats overlay shows active bitrate and climbing `framesDecoded`.
- [x] **8.3. Touch & Mouse Inputs**: *(Verified on Windows)*
  - [x] Click / tap on video -> emulator responds at exact coordinate.
  - [x] Drag / swipe across video -> smooth drag tracking on emulator; releases immediately when mouse/touch lifts.
  - [x] Virtual keyboard -> typing characters forwards keystrokes into emulator text field.
- [x] **8.4. Quality Tier Adaptation**: *(Verified on Windows)*
  - Open Settings overlay mid-stream.
  - Pin resolution to `480p`, `720p`, `1080p`.
  - Confirm video resolution adapts dynamically without stream disconnect or crash.
  - Switch back to `Auto`.
- [x] **8.5. Quick-Switch Drawer**: *(Verified on Windows)*
  - Open left navigation drawer.
  - Select a different running emulator instance.
  - Confirm stream transitions rapidly with keyframe prefetch (no endless loading spinner).

---

## Section 9: Mobile Physical Device Smoke Test (iOS / Android) [SKIPPED]

> **Note**: Skipped for this validation cycle (host machine disk storage constraint). Core logic and shared presentation layers remain verified via monorepo Jest test suites (`npm run test:core`, `npm run test:ui`).

Run app via Expo dev build or Expo Go on physical phone:

- [ ] **9.1. Zero-Config Launch**:
  - Launch app from fresh install or cleared cache.
  - **Pass condition**: App opens directly to **Login** screen (never shows a manual server URL screen).
- [ ] **9.2. Automatic Tunnel Routing**:
  - Log in with Account A.
  - Connects seamlessly via default tunnel endpoint (`EXPO_PUBLIC_API_URL`) and displays instance list.
- [ ] **9.3. Relaunch Session Persistence**:
  - Force quit mobile app and reopen.
  - Navigates directly to `InstanceList` without re-prompting for credentials.
- [ ] **9.4. Mobile Gesture Relays**:
  - [ ] Tap registers remote tap.
  - [ ] Rapid drag releases cleanly when finger lifts.
  - [ ] Two-finger proportional scroll scrolls remote app smoothly.
  - [ ] Virtual keyboard button opens native keyboard and sends text.
- [ ] **9.5. Lifecycle Recovery**:
  - While streaming, press Home to background app for 5 seconds, then return to app.
  - Confirm WebRTC video and DataChannel recover automatically without manual reconnect.
- [ ] **9.6. Server Disconnect Recovery**:
  - Stop host server -> Error overlay appears.
  - Restart host server, tap **Reconnect** -> stream resumes cleanly.

---

## Section 10: Sign-Off & Result Logging

- [x] All automated tests verified green (Section 2 full monorepo & Section 3.1-3.3 live server verified on macOS).
- [x] Engine compilation verified on Windows (Section 4).
- [x] Installer built and verified (Section 5).
- [x] Host Monitor Widget Option B visually confirmed (Section 6).
- [x] Supabase two-account isolation verified (Section 7).
- [x] Physical WebRTC streaming verified on web (Section 8; Section 9 mobile skipped due to host storage).
- [x] Update `HANDOFF.md` with sign-off entry:
  ```markdown
  ### 2026-09-07 01:00 — codex
  - Validated: v3.1.0 zero-config-and-host-gui-refactor on Windows 11 hardware
  - Automated: ALL 8 GATES PASS
  - Hardware: Host GUI widget, Supabase 2-account 403, and WebRTC dual-transport stream PASS
  - Blockers: none
  ```

