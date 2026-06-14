# WindowControl

Stream specific Windows 11 application windows to your iPhone over Tailscale.

## Features

- Window-specific capture — pick any open app, not the full screen
- MJPEG streaming at up to 30 fps
- Touch input: tap to click, two-finger scroll, pinch to zoom
- Virtual keyboard relay
- Auto-reconnect if connection drops
- Tailscale integration for secure remote access from anywhere
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

## Stream Quality

| Preset | JPEG Quality | Use Case |
|--------|-------------|----------|
| Low    | 40          | Slow / remote connection |
| Medium | 65          | Balanced |
| High   | 85          | Fast LAN / Tailscale |

Configurable in both the launcher and the iPhone UI.

## Building from Source

### Prerequisites

- Python 3.12+
- [uv](https://github.com/astral-sh/uv)
- [Inno Setup 6](https://jrsoftware.org/isdl.php) (Windows only, for installer)

### Run from source (Windows)

```bash
uv sync
uv run python src/main.py
```

### Build installer (Windows)

```bat
cd build
build_installer.bat
```

Produces `release/WindowControlInstaller.exe`.

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
  main.py              # Entry point
  config.py            # Ports, quality settings, paths
  gui/
    launcher.py        # PyQt5 launcher window
    window_list.py     # Window picker widget
    tray.py            # pystray system tray
  server/
    app.py             # FastAPI app factory
    stream.py          # Capture loop + MJPEG generator
    window_manager.py  # Win32 window enumeration
    input_handler.py   # Touch → Win32 input
    preview.py         # Window thumbnail generator
    tailscale.py       # Tailscale IP detection
  client/
    index.html         # iPhone web app
    app.js             # Stream + touch input
    windows_panel.js   # Swipe drawer
    style.css          # Mobile styles
    manifest.json      # PWA manifest
  stubs/               # Mac dev stubs for win32 + mss
build/
  window_control.spec  # PyInstaller spec
  build.bat            # Build EXE
  build_installer.bat  # Build EXE + installer
  installer.iss        # Inno Setup 6 script
.github/workflows/
  build.yml            # CI: build installer on tag push
tests/                 # pytest suite (43 tests)
```

## License

MIT
