# WindowControl

Stream specific Windows 11 application windows to your iPhone over Tailscale.

## Features

- Window-specific capture — pick any open app, not the full screen
- WebRTC (WHEP) streaming via a bundled native engine (`engine.exe`), one
  instance per selected window, each on its own dynamically assigned port
- Touch input, keyboard relay, and clicks delivered over a WebRTC
  DataChannel (no polling)
- Auto-reconnect if connection drops
- Tailscale integration for secure remote access from anywhere; embedded
  STUN keeps WebRTC working over Tailscale and LAN
- PWA — add to Home Screen on iPhone for full-screen experience
- System tray with Show / Stop / Exit controls

## Download

Grab the latest `WindowControlInstaller.exe` from the [Releases](../../releases) page. No Python required.

## Requirements

- Windows 11
- [Tailscale](https://tailscale.com/download) on both PC and iPhone (recommended; LAN-only without it)

## Connecting from iPhone

1. Install and sign into Tailscale on both your Windows PC and iPhone
2. Launch WindowControl — it appears in the system tray
3. Click **Start Server**
4. Select the window you want to stream
5. Scan the QR code in the launcher (or open the URL shown)
6. Stream appears full-screen on your iPhone
7. Swipe up from the bottom edge to switch windows
8. In Safari: **Share → Add to Home Screen** for full-screen PWA mode

### Without Tailscale (LAN only)

Works on the same Wi-Fi network. Use the LAN IP shown in the launcher.

## Access and pairing

EmuCtrl answers only devices on the same local network or on your Tailscale
network. Requests from any other address are refused.

A device has to be paired once before it can use the app:

1. On the PC, open EmuCtrl Host and click **Pair device**. A 6-digit code
   appears for 5 minutes.
2. On the phone or browser, open the app and enter the code. On the mobile
   app, also enter the PC's address (for example `100.101.102.103:8080`).
3. The device is remembered. Remove it from **Paired Devices** in the host
   window to revoke its access.

The window on the PC itself needs no pairing.

Traffic on the local network is plain HTTP, so anyone able to intercept
traffic on that network can read a pairing code or a device's token.
Tailscale traffic is encrypted. On networks you do not control, connect over
Tailscale.

## Troubleshooting

Stream won't play, or the engine won't start? See
[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — covers the recurring
Safari mDNS / STUN WebRTC bug (`write queue is full`) and how to read
`engine.exe`'s per-instance logs and admin-loopback health endpoint.

## Streaming Quality

The server encodes video at one of five quality tiers:

| Tier | Resolution | Max Bitrate | Max FPS |
|------|-----------|-------------|--------|
| 360  | up to 360p | ~0.8 Mbps | 30 |
| 480  | up to 480p | ~2 Mbps | 30 |
| 720  | up to 720p | ~4 Mbps | 30 |
| 1080 | up to 1080p | ~8 Mbps | 60 |
| 1440 | up to 1440p | ~12 Mbps | 60 |

**Adaptive:** The client monitors network conditions (packet loss, RTT) every 5 seconds and steps the tier down when the link is congested. It does not step back up on its own; pick a higher tier in stream settings. On a relayed Tailscale connection (`tailscale ping` reports `via DERP`), use 360.

**Manual control:** Override the active tier anytime via the UI or HTTP API (`POST /instances/{serial}/quality {tier: 360|480|720|1080|1440}`).

## Building from Source

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv)
- [Inno Setup 6](https://jrsoftware.org/isdl.php) (Windows only, for installer)
- CMake + vcpkg (Windows only, to build `engine/`; see
  [engine/BUILD_WINDOWS.md](engine/BUILD_WINDOWS.md))

### Run from source (Windows)

```bash
uv sync
uv run python src/main.py
```

Requires a built `engine.exe` staged at `src/assets/engine/engine.exe` (see
`build/build.bat` below, or build `engine/` directly with CMake).

### Build installer (Windows)

```bat
cd build
build_installer.bat
```

`build.bat` (invoked by `build_installer.bat`) builds `engine/` in Release
mode, stages `engine.exe` and its runtime DLLs into `src/assets/engine`, then
runs PyInstaller. Produces `release/WindowControlInstaller.exe`. The installer
adds and removes a named Windows Firewall program rule
(`WindowControl-Engine`) for the installed `engine.exe`.

### CI build (GitHub Actions)

Push a tag — the workflow builds and attaches the installer to the release automatically:

```bash
git tag v1.0.0
git push origin v1.0.0
```

### Development on Mac

All Win32 and mss APIs are stubbed — tests run fully on Mac:

```bash
uv sync
uv run pytest tests/ -v
```

## File Structure

```
src/
  main.py                    # Entry point
  config.py                  # Ports, quality settings, paths
  gui/
    launcher.py              # PyQt5 launcher window
    window_list.py           # Window picker widget
    tray.py                  # pystray system tray
  server/
    app.py                   # FastAPI app factory (instance/select/quality routes)
    engine_orchestrator.py   # Discovers windows, owns per-instance engine runtimes
    engine_runtime.py        # Launches/reconnects/monitors one engine.exe instance
    engine_process.py        # engine.exe subprocess launcher
    engine_admin.py          # Loopback admin client (health/reconnect/keyframe)
    window_manager.py        # Win32 window enumeration
    tailscale.py             # Tailscale IP detection
    stun_server.py           # Embedded STUN, bound to the Tailscale interface
  client/
    index.html               # iPhone web app
    app.js                   # WHEP stream + DataChannel touch/keyboard input
    windows_panel.js         # Swipe drawer
    style.css                # Mobile styles
    manifest.json            # PWA manifest
  assets/
    engine/                  # engine.exe + runtime DLLs, staged by build.bat
    scrcpy/                  # downloaded by scripts/download_assets.py
  stubs/                     # Mac dev stubs for win32 + mss
engine/                      # C++ WebRTC engine (Windows-only), see engine/BUILD_WINDOWS.md
build/
  window_control.spec        # PyInstaller spec
  build.bat                  # Build engine + stage assets + build EXE
  build_installer.bat        # Build EXE + installer
  installer.iss               # Inno Setup 6 script; owns the engine firewall rule
.github/workflows/
  build.yml                  # CI: build engine, run full engine_tests.exe, build installer
tests/                       # pytest suite
```

## License

MIT
