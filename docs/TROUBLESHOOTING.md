# Troubleshooting

## WebRTC stream won't play — `write queue is full` / `deadline exceeded`

**Symptom.** mediamtx logs repeat:

```
WAR [WebRTC] [session ...] write queue is full
INF [WebRTC] [session ...] closed: deadline exceeded while waiting connection
```

The stream never appears. In the browser console the ICE state reaches
`checking` but never `connected`.

This is a **recurring** bug. If you hit it again, the fix below is already in
the code — check it's actually running (git pull on the server, hard-reload the
browser) before assuming a new cause.

### Root cause: Safari mDNS host candidate

Safari only emits an mDNS (`.local`) host ICE candidate for privacy, and offers
**no flag to disable it** (unlike Chrome's
`chrome://flags/#enable-webrtc-hide-local-ips-with-mdns`). Example candidate:

```
candidate:... udp ... 3b7859fa-....local 65442 typ host ...
```

mediamtx cannot resolve `.local` over Tailscale, so the candidate pair never
forms, media never flows, mediamtx's send buffer fills (`write queue is full`),
and the session times out.

### Why public STUN does NOT fix it

Adding a public STUN server (e.g. `stun:stun.l.google.com:19302`) makes Safari
gather a server-reflexive (`srflx`) candidate — but the STUN query exits over
the public internet, so the reflected address is the ISP / Cloudflare-WARP
**public** IP:

```
candidate:... udp ... 104.28.71.152 41893 typ srflx ...
```

mediamtx lives on a Tailscale IP (`100.x.x.x`) and cannot reach `104.x`, so this
`srflx` candidate is useless. Public STUN is a dead end here.

### The fix: embedded STUN bound to the Tailscale IP

We run a minimal STUN Binding server
([`src/server/stun_server.py`](../src/server/stun_server.py)) bound to the
**Tailscale interface** on UDP `3478`. Because it lives on the Tailscale IP, the
browser's Binding request routes *over Tailscale*, so the source address the
STUN server sees — and returns as the `srflx` candidate — is the browser's
**Tailscale** IP (`100.x`), which mediamtx can reach directly. No relay hop.

Wiring:

| Piece | Location | Role |
|-------|----------|------|
| STUN server | `src/server/stun_server.py` | UDP Binding responder |
| Start / rebind | `InstanceManager._ensure_stun` in `src/server/instance_manager.py` | binds STUN to the current Tailscale IP; rebinds if the IP changes |
| Advertise to client | `/select` in `src/server/app.py` sends `stun_url` per instance | `stun:<tailscale-ip>:3478` |
| Use it | `initWebRTC` in `src/client/app.js` | `RTCPeerConnection({ iceServers: [{ urls: stun_url }] })` |
| Firewall | `src/main.py` | opens UDP `3478` inbound |
| Config | `STUN_PORT` in `src/config.py` | `3478` |

### Also required: non-trickle WHEP needs the candidate in the offer

mediamtx's WHEP is **non-trickle** — the answer is a one-shot HTTP response, so
the offer POSTed by the client must already contain the full candidate list.
`initWebRTC` calls `waitForIceGatheringComplete(pc)` **before** POSTing, so the
`srflx` candidate is present. Skipping this wait was why earlier STUN attempts
appeared to do nothing.

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

## mediamtx fails to start — `listen udp :8000: bind`

**Symptom.** mediamtx logs at startup:

```
ERR listen udp :8000: bind: Only one usage of each socket address
    (protocol/network address/port) is normally permitted.
```

WebRTC then never works because the ICE UDP mux never binds.

### Causes and fixes

1. **Orphan `mediamtx.exe` from a crashed/force-closed prior run** still holds
   the port. `MediamtxManager._stop_locked` only kills the process *this* run
   spawned, so an orphan is invisible to it. `_reap_orphan_mediamtx()` in
   `src/server/mediamtx_manager.py` runs `taskkill /F /IM mediamtx.exe` before
   every start to clear it. Manual clear if needed:

   ```
   taskkill /F /IM mediamtx.exe
   ```

2. **Default port collision.** mediamtx's default WebRTC UDP mux is `:8000`,
   which collides with other software. We pin it explicitly via
   `webrtcLocalUDPAddress: :8288` (`WEBRTC_UDP_PORT` in `src/config.py`) and
   open UDP `8288` in the firewall.

Note: changing the port does not help if the collision is another *mediamtx*
orphan — it would grab the new port too. Keep the reaper.

---

## ffmpeg fails to copy video codec — `Non-monotonic DTS` or codec errors

**Symptom.** ffmpeg logs show errors like:

```
Non-monotonic DTS in output file
Stream ends prematurely
```

## Slow first frame / slow instance switch (~20-30s black screen)

**Symptom:** ICE connects immediately (browser console shows `[ice] state:
connected` and `ontrack fired`), but the video stays black for 20-30s before
`loadedmetadata` / `playing`. Same delay on the first select and on every
instance switch.

### Root cause: device encoder emits keyframes too rarely

WebRTC cannot start decoding until it receives an IDR keyframe. scrcpy asks the
device to emit one every ~2s via `video_encoder_options=i-frame-interval=2`, but
**some device MediaCodec encoders ignore that hint entirely** (observed on ASUS
AI2205) and emit an IDR only every 20-30s. Time-to-first-frame is then bounded by
that interval.

### Fix: copy-mux + source-side IDR request (TYPE_RESET_VIDEO)

`build_ffmpeg_args` now uses `-c:v copy` (no re-encode). The device already emits
H.264 at the tier's bitrate/fps, so libx264 only burned CPU (full decode+encode
per frame, per active instance) and added ~1 frame of latency.

Keyframes are forced at the **source** instead of by an ffmpeg GOP:
`ScrcpyControl.request_idr()` sends `TYPE_RESET_VIDEO` (control message type
`0x11`, a bodyless 1-byte message), which makes scrcpy-server drive the device
encoder to emit an IDR on demand. `ScrcpySession._stream_loop` requests one right
after the control socket connects (fast first-frame) and then every ~2s on a
heartbeat thread. A WHEP subscriber that joins between requests waits at most ~2s
for a keyframe — the same cadence the forced ffmpeg GOP used to provide, without
the transcode.

This is why the earlier `-c:v copy` attempt (reverted in commit `7083f8e`)
failed and this one does not: that attempt had **no way to force an IDR** and
inherited the device encoder's ~20-30s rare-IDR behavior. `TYPE_RESET_VIDEO` is
the missing piece.

> **Do not remove `-use_wallclock_as_timestamps 1`.** Raw H.264 from scrcpy
> carries no container timestamps; without it ffmpeg guesses 25fps, the RTSP
> muxer stalls on non-monotonic DTS, and mediamtx times out the publish (~10s
> `i/o timeout`), dropping every instance. This was the FIRST copy-mux failure
> (commit `15a2d4e`) — see the section above.
