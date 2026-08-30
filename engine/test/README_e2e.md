# engine/test/README_e2e.md

## Manual end-to-end validation (Phase 0+1 done when this passes)

Prerequisites:
- `engine.exe` built on the Host PC (Task 10).
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

Engine invocation (dev mode, no auth):

```powershell
$env:ENGINE_WHEP_CAPABILITY_SECRET = ""  # unset or empty = WHEP auth disabled
engine.exe my-instance 27183
```

This prints a ready record JSON line to stdout containing `whep_port` (e.g., 8000).

Required/optional environment variables (if needed for your network):

```
ENGINE_WHEP_CAPABILITY_SECRET   (unset = WHEP auth disabled, dev-only)
ENGINE_LOCAL_ICE_SERVERS        (comma-separated, may be empty for pure-LAN)
ENGINE_SIGNALING_URL            (unset = public/VPS path disabled)
ENGINE_SIGNALING_TOKEN          (JWT, only used if ENGINE_SIGNALING_URL is set)
ENGINE_PUBLIC_ICE_SERVERS       (comma-separated, only used if signaling enabled)
```

Steps:

1. Start a fresh scrcpy-server on a forwarded port (see Prerequisites above).

2. On the Host PC, run the engine:

       engine.exe my-instance 27183

   Expected: Engine console prints a JSON ready record line like:
   ```json
   {"instance_name":"my-instance","pid":1234,"whep_port":8000,"admin_port":8001,"generation":1,"width":720,"height":1280}
   ```

3. On any machine with a browser, open `test_page.html` with the WHEP URL
   as a query param (from the ready record's `whep_port` above):

       http://localhost:8000/test_page.html?whep=http://localhost:8000/whep

   Page shows "connected, waiting for video..." then "ICE: connected/completed" then "receiving video".

4. Expected within a few seconds:
   - Engine console prints `scrcpy handshake: device=... <W>x<H>`, then
     `Streaming started`.
   - Browser page status updates to `ICE: connected` (or `completed`), then
     `receiving video`.
   - The `<video>` element shows the live emulator/device screen content.

5. Check the browser's `chrome://webrtc-internals` (or Firefox's
   `about:webrtc`) to confirm the active candidate pair — note whether it's
   `host`/`host` (same-network P2P), `srflx`/`srflx` (STUN-assisted P2P), or
   `relay`/`relay` (TURN fallback). All three are valid PoC-pass outcomes.

6. Click on the video in the browser. Expected: the emulator/device
   registers a tap at the corresponding screen location (visible in the
   video a moment later — e.g. tapping an app icon opens it). This
   confirms the DataChannel -> `ScrcpyControlClient::SendTouch` ->
   control-socket path end to end.

7. Stop the engine with Ctrl+C — confirm the browser's video freezes/ends
   and `pc.iceConnectionState` transitions away from `connected`.

If any step fails, the relevant earlier task's automated tests (Tasks 1-9)
should be re-checked first — this end-to-end pass is the integration
capstone, not a substitute for the unit-level tests.
