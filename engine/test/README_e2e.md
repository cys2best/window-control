# engine/test/README_e2e.md

## Manual end-to-end validation (Phase 0+1 done when this passes)

Prerequisites:
- Signaling server deployed on VPS (Task 4) and running.
- coturn deployed on VPS (Task 1) and running.
- `engine.exe` built on the Host PC (Task 8).
- A scrcpy-server running against a real Android emulator (e.g. LDPlayer)
  or physical device, started manually for this plan's scope — the Python
  control-plane doesn't automate this yet (that's Phase 3). Use
  `src/server/scrcpy_session.py`'s `_start_server()` function as the exact
  reference for the adb push/launch/forward sequence, or run it via a short
  Python REPL session against the existing codebase:

      uv run python -c "
      from server.scrcpy_session import _start_server
      from server.adb_manager import _find_adb
      adb = _find_adb()
      _start_server(adb, '<device-serial>', 27183, scid=1, tier='720')
      "

  This pushes the server jar, launches it, and sets up `adb forward
  tcp:27183 localabstract:scrcpy_00000001`. Confirm with `adb -s
  <device-serial> forward --list` that the forward is active.

Steps:

1. On the VPS, confirm both services are up:

       sudo systemctl status webrtc-signaling coturn

2. On any machine with a browser, open `engine/test/test_page.html`
   (via `file://` or a simple `python -m http.server` in `engine/test/`)
   with query params pointing at the VPS:

       test_page.html?signaling=ws://VPS_IP:8443&session=poc-session-1&ice=turn:poc-user:poc-secret-change-me@VPS_IP:3478

   Page shows "signaling connected, waiting for offer".

3. On the Host PC, run the engine against the forwarded scrcpy port:

       engine.exe 27183 ws://VPS_IP:8443 poc-session-1 "turn:poc-user:poc-secret-change-me@VPS_IP:3478"

4. Expected within a few seconds:
   - Engine console prints `scrcpy handshake: device=... <W>x<H>`, then
     `Streaming started`.
   - Browser page status updates to `ICE: connected` (or `completed`), then
     `receiving video`.
   - The `<video>` element shows the live emulator/device screen content.

5. Check the browser's `chrome://webrtc-internals` (or Firefox's
   `about:webrtc`) to confirm the active candidate pair — note whether it's
   `host`/`host` (same-network P2P), `srflx`/`srflx` (STUN-assisted P2P), or
   `relay`/`relay` (TURN fallback). All three are valid PoC-pass outcomes;
   `relay` specifically confirms Task 1's coturn is functioning correctly as
   the fallback path.

6. Click on the video in the browser. Expected: the emulator/device
   registers a tap at the corresponding screen location (visible in the
   video a moment later — e.g. tapping an app icon opens it). This
   confirms the DataChannel -> `ScrcpyControlClient::SendTouch` ->
   control-socket path end to end.

7. Stop the engine with Ctrl+C — confirm the browser's video freezes/ends
   and `pc.iceConnectionState` transitions away from `connected`.

If any step fails, the relevant earlier task's automated tests (Tasks 1-8)
should be re-checked first — this end-to-end pass is the integration
capstone, not a substitute for the unit-level tests.
