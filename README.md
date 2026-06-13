# WindowControl

Stream specific Windows 11 application windows to your iPhone over Tailscale.

## What It Does

WindowControl runs on Windows 11 and captures individual application windows (not the full screen). It serves them as a live MJPEG stream to your iPhone via a mobile-optimized web app. You can interact with the remote window using touch gestures directly on your phone.

**Features:**
- Window-specific capture (pick any open window)
- MJPEG streaming at up to 30fps
- Touch input: tap to click, two-finger scroll, pinch to zoom the view
- Virtual keyboard relay
- Auto-reconnect if connection drops
- Tailscale integration for secure remote access
- PWA: add to Home Screen on iPhone for full-screen experience
- System tray with Show/Stop/Exit controls

## Requirements

### Windows (runtime)
- Windows 11
- Python 3.12+ (or use the pre-built installer)
- [Tailscale](https://tailscale.com/download) (recommended for remote access; LAN-only without it)

### Optional (for best streaming performance)
- [PyTurboJPEG](https://github.com/lilohuang/PyTurboJPEG) + libturbojpeg — enables faster JPEG encoding. Falls back to Pillow if not installed.

## Development Setup (Mac)

```bash
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create venv and install dependencies
uv sync

# Run tests (Win32 APIs are stubbed for Mac)
uv run pytest tests/ -v
```

All Win32 and mss APIs are stubbed for Mac development. Tests run fully on Mac.

## Running from Source (Windows)

```bash
# Install dependencies
uv sync

# Run
uv run python src/main.py
```

The launcher window will appear. Click **Start Server**, then select a window from the list.

## Building

### Prerequisites (Windows)
- [Inno Setup 6](https://jrsoftware.org/isdl.php) installed to default path

### Build installer

```bat
cd build
build_installer.bat
```

This runs PyInstaller to create `dist/WindowControl.exe`, then Inno Setup to create `release/WindowControlInstaller.exe`.

### Build EXE only (no installer)

```bat
cd build
build.bat
```

## Connecting from iPhone

1. Install and sign into Tailscale on both your Windows PC and iPhone
2. Launch WindowControl on Windows and click **Start Server**
3. Select the window you want to stream
4. Scan the QR code shown in the launcher (or open the URL on your iPhone)
5. The stream will appear full-screen
6. Swipe up from the bottom to switch windows
7. **Add to Home Screen** in Safari for full-screen PWA mode

### Without Tailscale (LAN only)
Works on the same Wi-Fi network. Use the LAN IP shown in the launcher.

## File Structure

```
src/
  main.py              # Entry point — wires GUI, server, tray
  config.py            # Ports, quality settings, paths
  gui/
    launcher.py        # PyQt5 main window (single-column layout)
    window_list.py     # Window picker widget
    tray.py            # pystray system tray icon
  server/
    app.py             # FastAPI app factory
    stream.py          # Capture loop + MJPEG generator
    window_manager.py  # Win32 window enumeration
    input_handler.py   # Touch->Win32 input translation
    preview.py         # Window thumbnail generator
    tailscale.py       # Tailscale IP detection
  client/
    index.html         # iPhone web app shell
    app.js             # Stream display + touch input
    windows_panel.js   # Swipe drawer + window switching
    style.css          # Mobile-optimized styles
    manifest.json      # PWA manifest
  assets/
    icon.ico           # App icon
    tray_icon.png      # Tray icon
  stubs/
    win32_stub.py      # Mac dev stub for win32gui/api/con
    mss_stub.py        # Mac dev stub for mss screen capture
build/
  window_control.spec  # PyInstaller spec
  build.bat            # Build EXE
  build_installer.bat  # Build EXE + installer
  installer.iss        # Inno Setup 6 script
tests/                 # pytest test suite (43 tests)
```

## Stream Quality

Three quality presets (configurable in launcher and iPhone UI):

| Preset | JPEG Quality | Use Case |
|--------|-------------|----------|
| Low    | 40          | Slow connection |
| Medium | 65          | Balanced |
| High   | 85          | Fast LAN/Tailscale |

## License

MIT
