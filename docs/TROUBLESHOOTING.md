# Troubleshooting

## WebRTC stream won't play — `write queue is full` / `deadline exceeded`

**Symptom.** The stream never appears. In the browser console the ICE state
reaches `checking` but never `connected`.

This is a **recurring** bug. If you hit it again, the fix below is already in
the code — check it's actually running (git pull on the server, hard-reload
the browser) before assuming a new cause.

### Root cause: Safari mDNS host candidate

Safari only emits an mDNS (`.local`) host ICE candidate for privacy, and offers
**no flag to disable it** (unlike Chrome's
`chrome://flags/#enable-webrtc-hide-local-ips-with-mdns`). Example candidate:

```
candidate:... udp ... 3b7859fa-....local 65442 typ host ...
```

The engine cannot resolve `.local` over Tailscale, so the candidate pair never
forms and media never flows.

### Why public STUN does NOT fix it

Adding a public STUN server (e.g. `stun:stun.l.google.com:19302`) makes Safari
gather a server-reflexive (`srflx`) candidate — but the STUN query exits over
the public internet, so the reflected address is the ISP / Cloudflare-WARP
**public** IP:

```
candidate:... udp ... 104.28.71.152 41893 typ srflx ...
```

The engine listens on a Tailscale IP (`100.x.x.x`) and cannot reach `104.x`, so
this `srflx` candidate is useless. Public STUN is a dead end here.

### The fix: embedded STUN bound to the Tailscale IP

We run a minimal STUN Binding server
([`src/server/stun_server.py`](../src/server/stun_server.py)) bound to the
**Tailscale interface** on UDP `3478`. Because it lives on the Tailscale IP, the
browser's Binding request routes *over Tailscale*, so the source address the
STUN server sees — and returns as the `srflx` candidate — is the browser's
**Tailscale** IP (`100.x`), which the engine can reach directly. No relay hop.

Wiring:

| Piece | Location | Role |
|-------|----------|------|
| STUN server | `src/server/stun_server.py` | UDP Binding responder |
| Start / rebind | `InstanceManager._ensure_stun` in `src/server/instance_manager.py` | binds STUN to the current Tailscale IP; rebinds if the IP changes |
| Advertise to client | engine-select response includes `ice_servers` | `stun:<tailscale-ip>:3478` (and TURN, for public/remote sessions) |
| Use it | the browser client's WHEP/WebRTC setup in `src/client/app.js` | `RTCPeerConnection({ iceServers })` |
| Firewall | `src/main.py` | opens UDP `3478` inbound |
| Config | `STUN_PORT` in `src/config.py` | `3478` |

### Verifying the fix

- **Server log:** `[stun] listening on 100.x.x.x:3478`
- **Browser console:** a candidate line with `typ srflx` and a **`100.x`**
  (Tailscale) address — not `104.x`.
- **ICE state** progresses `checking` → `connected`; video plays.

If `srflx` still shows a `104.x` public IP, the browser's route to
`100.x:3478` did not go over Tailscale — check Tailscale is up on the browser
device and that UDP `3478` is open on the server.

> **Safari caches `app.js` aggressively.** After any client change, hard-reload
> with **Cmd+Option+R**, or the old script (often with public/no STUN) keeps
> running and the bug looks unfixed.

---

## engine.exe won't start, or a selected instance never goes live

Each selected window is served by its own `engine.exe` instance, launched by
`EngineOrchestrator`/`EngineRuntime` (`src/server/engine_orchestrator.py`,
`src/server/engine_runtime.py`) with a **dynamically assigned** WHEP port (no
fixed port to collide on) and a loopback-only admin port.

### Diagnosing a stuck or crashed instance

1. **Per-instance engine logs.** Each `engine.exe` process's stdout/stderr is
   captured by the Python launcher; check the WindowControl app's own log
   output (and, on the installed build, `%ProgramData%\WindowControl\` if a
   crash log was written) for `[engine]`-prefixed lines naming the failing
   instance.
2. **Admin-loopback health.** `EngineRuntime` polls the instance's admin HTTP
   API (`/admin/health`, `/admin/reconnect`, `/admin/keyframe`) on
   `127.0.0.1:<admin_port>` — never exposed off-box. A stalled instance
   usually shows up as repeated `[engine] health failed for <instance>: ...`
   log lines; that triggers an automatic reconnect.
3. **Orphaned processes.** If WindowControl was force-killed, an orphaned
   `engine.exe` can hold a device/window resource. Clear it manually:

   ```
   taskkill /F /IM engine.exe
   ```

4. **Missing/blocked firewall rule.** The installer creates and removes a
   single named inbound rule, `WindowControl-Engine`, scoped to the installed
   `engine.exe` program path (not to a specific port, since ports are
   per-instance and dynamic). If a manual firewall change removed it, WHEP
   connections from other devices on the LAN/Tailscale may fail even though
   the stream works on `localhost`. Recreate it via `netsh advfirewall
   firewall add rule name="WindowControl-Engine" dir=in action=allow
   program="<install dir>\assets\engine\engine.exe" enable=yes` or reinstall.

### Building/running the engine directly

See [engine/BUILD_WINDOWS.md](../engine/BUILD_WINDOWS.md) for the CMake/vcpkg
build, and [engine/test/README.md](../engine/test/README.md) /
[engine/test/README_e2e.md](../engine/test/README_e2e.md) for running
`engine_tests.exe` (including the live signaling suite, which needs the
repository's Node relay at `infra/vps/signaling`).

---

## Slow first frame / slow instance switch (~20-30s black screen)

**Symptom:** ICE connects immediately, but the video stays black for 20-30s
before the first frame decodes. Same delay on the first select and on every
instance switch.

### Root cause: device encoder emits keyframes too rarely

WebRTC cannot start decoding until it receives an IDR keyframe. scrcpy asks the
device to emit one every ~2s via `video_encoder_options=i-frame-interval=2`, but
**some device MediaCodec encoders ignore that hint entirely** (observed on ASUS
AI2205) and emit an IDR only every 20-30s.

### Fix: source-side IDR request

The engine requests an IDR from the device (`TYPE_RESET_VIDEO`, scrcpy control
message `0x11`) right after connecting to the scrcpy source, and again on a
heartbeat and whenever a fresh WHEP subscriber joins — the same behavior the
Python `ScrcpyControl.request_idr()` provided in the legacy pipeline, now owned
by the engine. A subscriber that joins between heartbeats waits at most ~2s for
a keyframe. If you see a persistent 20-30s stall, check the engine log for
whether `TYPE_RESET_VIDEO` requests are actually reaching the device (device
disconnected, or a busy control socket).
