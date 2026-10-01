# Windows Validation Runbook — v3.1.0 (Zero-Config & Host GUI Refactor)

**Purpose**: Comprehensive validation runbook for Windows host hardware and client streaming. 

> [!TIP]
> **Automate First!** Run `.\engine\verify-all.ps1` (or `uv run python scripts/verify_all.py`) to execute all unit, integration, route, and headless GUI suites in one command with zero manual intervention. Only the physical hardware checks in the minimal checklist below require human eyes.

---

## Executive Summary Checklist (Follow in Order)

### Phase 1: Full Automated Validation (0 Manual Steps)
- [ ] **Run all automated gates**:
  ```powershell
  .\engine\verify-all.ps1
  ```
  *(Verifies: Python backend tests, headless PyQt5 launcher widget, TypeScript core, shared UI and web client tests, Next.js static export build, and export artifact integrity).*

### Phase 2: Packaging & Installer Verification
- [ ] **Build the installer**:
  ```powershell
  cd build; .\build.bat; cd ..
  ```
- [ ] **Check the package**: follow section 6 below (installed service, firewall rule `WindowControl-Engine`, packaged assets, no `/setup`, no `pywebview` modules).

### Phase 3: Physical Hardware Checks (Only 3 Manual Gates)
- [ ] **1. Minimal Host Monitor Widget (Option B)**:
  - From the Windows system tray icon, click **Show**.
  - Confirm: compact ~400px card displays green status dot, port `8080`, detected LAN and Tailscale IPs, and active stream counter.
  - Confirm: clicking **Minimize to Tray** hides window; clicking **[X]** minimizes to tray without stopping the server.
- [ ] **2. Device Pairing**: complete the pairing steps in section 3 below.
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
  - Python tests: all pass (0 collection errors; `test_auto_unlock.py` and `test_launcher_widget.py` passing).
  - TypeScript Core, UI and web client suites: all pass.

---

### 3. Device Pairing Manual Gate
1. Start the host: `uv run python src\main.py`.
2. On the PC click **Pair device** in the launcher; pair a phone and a browser; reload and confirm no code is asked; remove the device and confirm the client returns to pairing; stream from a device on the LAN without Tailscale and from a Tailscale device.

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

### 7. Reporting Results
Update `HANDOFF.md` with:
- Date and commit SHA tested.
- Automated suite pass confirmation (`.\engine\verify-all.ps1`).
- Confirmation of the 3 hardware gates (Host GUI, Device Pairing, Physical Streaming).
