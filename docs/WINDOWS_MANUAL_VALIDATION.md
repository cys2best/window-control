# Windows Validation Runbook — v3.1.0 (Zero-Config & Host GUI Refactor)

**Purpose**: Comprehensive validation runbook for Windows host hardware and client streaming. 

> [!TIP]
> **Automate First!** Run `.\engine\verify-all.ps1` (or `uv run python scripts/verify_all.py`) to execute all unit, integration, route, headless GUI, and relay suites in one command with zero manual intervention. Only the physical hardware checks in the minimal checklist below require human eyes.

---

## Executive Summary Checklist (Follow in Order)

### Phase 1: Full Automated Validation (0 Manual Steps)
- [ ] **Run all automated gates**:
  ```powershell
  .\engine\verify-all.ps1
  ```
  *(Verifies: 568 Python backend tests, headless PyQt5 launcher widget, 68 TypeScript core WebRTC session/signaling tests, 18 shared UI tests, 7 web client tests, Next.js static export build, export artifact integrity, and VPS WebRTC signaling relay).*
- [ ] **Run automated frontend/desktop cutover gates**:
  ```powershell
  .\engine\verify-frontend-cutover.ps1 -SkipManualGates -SkipInstaller
  ```
  *(Verifies: Live dev server startup, health `/health`, config `/auth/config`, web routes `/`, `/login`, `/instances`, `/stream`, absence of retired `/setup`, RSC payloads, and 401 unauthenticated access rejection).*

### Phase 2: Packaging & Installer Verification (Automated Subset)
- [ ] **Build the installer**:
  ```powershell
  cd build; .\build.bat; cd ..
  ```
- [ ] **Run automated installer package validation**:
  ```powershell
  .\engine\verify-frontend-cutover.ps1 -Only installed_app_launch,frozen_package_layout
  ```
  *(Verifies: Installed service starts from `Program Files\WindowControl`, firewall rule `WindowControl-Engine` matches `_internal\assets\engine\engine.exe`, packaged static assets exist in `_internal\web\`, `/setup` is absent, and no retired `pywebview` modules are bundled).*

### Phase 3: Physical Hardware Checks (Only 3 Manual Gates)
- [ ] **1. Minimal Host Monitor Widget (Option B)**:
  - From the Windows system tray icon, click **Show**.
  - Confirm: compact ~400px card displays green status dot, port `8080`, detected LAN and Tailscale IPs, VPS relay status, and active stream counter.
  - Confirm: clicking **Minimize to Tray** hides window; clicking **[X]** minimizes to tray without stopping the server.
- [ ] **2. Supabase Two-Account Isolation**:
  - Open `http://<PC-IP>:8080/login` in a browser. Log in as Account A (claims machine on first request).
  - Open an incognito window and log in as Account B.
  - Confirm Account B does **not** see Account A's claimed devices, and direct API actions return `403`.
- [ ] **3. Dual-Transport WebRTC Stream**:
  - Click an instance to open the stream.
  - Confirm: live video displays, green network dot is visible, touch/click input and keyboard work smoothly.
  - Test mid-stream quality change (Auto / 720p / 1080p).

---

## Detailed Step-by-Step Reference

### 0. One-time machine setup
- [ ] Windows 11, real hardware or VM with display.
- [ ] **Visual Studio 2022** with "Desktop development with C++", **vcpkg** bootstrapped, **CMake >= 3.24**.
- [ ] **Python 3.11+** and [`uv`](https://docs.astral.sh/uv/) installed.
- [ ] **Node.js 20+** + npm.
- [ ] **ADB** on PATH with at least one LDPlayer emulator or physical Android device running.
- [ ] Environment variables configured: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, `VPS_SIGNALING_URL`.

---

### 1. Build Verification
```powershell
cd <repo-root>
uv sync
npm install

# Engine (Release)
cmake -S engine -B engine\build -DCMAKE_TOOLCHAIN_FILE=<path-to-vcpkg>\scripts\buildsystems\vcpkg.cmake -DVCPKG_TARGET_TRIPLET=x64-windows
cmake --build engine\build --config Release
```
- [ ] **Pass condition**: `engine\build\Release\engine.exe` exists with 0 build errors.

```powershell
npm run build -w apps/web
```
- [ ] **Pass condition**: `apps/web/out/` contains `index.html`, `login.html`, `instances.html`, `stream.html`, `404.html`, `manifest.json`, and `.txt` RSC payloads. Notice: `setup.html` must **not** exist (retired).

---

### 2. Automated Offline Suites
```powershell
uv run pytest tests/ apps/desktop/ -v
npm run test:core; npm run test:ui; npm test -w apps/web
```
- [ ] **Pass condition**:
  - Python tests: **568 passed** (0 collection errors; `test_auto_unlock.py` and `test_launcher_widget.py` passing).
  - TypeScript Core: **68 passed** (includes dual-transport session racing & signaling client).
  - TypeScript UI: **18 passed**.
  - Web client: **7 passed**.

---

### 3. Supabase Multi-User Auth Manual Gate
1. Start the host: `uv run python src\main.py`.
2. Open browser to `http://<PC-IP>:8080/login`.
3. Register/Login as Account A:
   - Device claimed via trust-on-first-use.
   - Instance list displays connected emulator.
4. Open private window, login as Account B:
   - **Critical check**: Account B cannot see Account A's device.
   - Any direct API call to `/instances/{serial}/select` returns `403 Forbidden`.

---

### 4. Core Dual-Transport Streaming Path
1. Open instance stream on web or mobile (`http://<PC-IP>:8080/stream` or mobile app).
2. **Pass condition**:
   - Live video stream paints (no black frame).
   - `framesDecoded` counter increments in stats overlay.
   - Network status dot indicates connected state.
   - Drag, click, and virtual keyboard input work without lag or sticky touches.
3. Switch instances via drawer -> stream switches rapidly with keyframe prefetch.
4. Adapt quality tier mid-stream -> video adapts smoothly without stream teardown or crash.

---

### 5. Desktop Shell: Option B Minimal Host Monitor
1. Locate WindowControl icon in the Windows taskbar system tray.
2. Click **Show**:
   - Minimal Host Monitor window opens (~400px width, ~460px height).
   - Header shows `WindowControl Host v3.1.0` with green running dot and `:8080`.
   - Network row displays Local LAN IP and Tailscale IP.
   - VPS Relay row displays connection status.
   - Streams row displays active viewer count.
3. Click **Minimize to Tray** -> window hides.
4. Click tray icon **Show** -> window reappears.
5. Click **[X]** window close button -> window minimizes to tray instead of quitting.
6. Click tray icon **Exit** -> server stops cleanly, process terminates.

---

### 6. Packaged Installer & Service Check
1. Build installer: `cd build; .\build.bat`.
2. Inspect `dist\WindowControl\`:
   - `WindowControl.exe` present.
   - `_internal\assets\engine\engine.exe` present.
   - `_internal\web\` contains web build artifacts (no `setup.html`).
   - Confirm **no** `pywebview` or WebView2 files bundled.
3. Run Inno Setup installer -> installs to `C:\Program Files\WindowControl\`.
4. Check firewall rule:
   ```powershell
   netsh advfirewall firewall show rule name="WindowControl-Engine"
   ```
   Rule points to `C:\Program Files\WindowControl\_internal\assets\engine\engine.exe`.
5. Launch installed app -> verify tray icon and Minimal Host Monitor widget function correctly.
6. Uninstall via Settings -> verifies clean removal with no orphaned firewall rules or files.

---

### 7. Leaked Install-Key Cross-Account Forgery Check
1. Locate install private key in `%LOCALAPPDATA%\WindowControl\`.
2. Copy key to a second machine / clean directory.
3. Run app and log in as Account B.
4. **Security Pass Condition**: Copied key only identifies original machine's session — Account B cannot spoof or hijack Account A's device stream.

---

### 8. Reporting Results
Update `HANDOFF.md` with:
- Date and commit SHA tested.
- Automated suite pass confirmation (`.\engine\verify-all.ps1`).
- Confirmation of the 3 hardware gates (Host GUI, 2-Account Auth, Physical Streaming).
