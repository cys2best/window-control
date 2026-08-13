# Performance Overhaul — Design

**Date:** 2026-08-13
**Status:** Approved

## Goal

Improve three performance dimensions of window-control:

1. **Streaming quality** — reach 1080p/1440p, auto-adapt 480p↔1440p by client network.
2. **Control latency** — reduce end-to-end video delay so touch feels game-grade.
3. **Instance switch** — eliminate the multi-second gap when switching instances.

## Current Architecture (baseline)

```
scrcpy-server (on-device H.264, bit_rate=4M, max_fps=30)
  → Python TCP read
  → ffmpeg RE-ENCODE (libx264 ultrafast, -b:v 4M, -g 60)   ← double encode
  → RTSP → mediamtx (one path per instance)
  → WHEP/WebRTC → browser
```

- All instances stream to mediamtx simultaneously (hot).
- Every browser switch = new `RTCPeerConnection` + full ICE gather (cap 2000ms) + WHEP POST + SDP. This is the switch gap.
- Quality hardcoded, no adaptivity.
- Input path (touch → WS JSON → TCP `send_touch`) is already low-latency (TCP_NODELAY, fire-and-forget). The felt "control delay" is dominated by the **video pipeline**, not input transport.

## Section 1 — Streaming: passthrough + quality tiers

**Remove ffmpeg re-encode.** scrcpy already emits clean H.264. ffmpeg becomes a pure muxer:

```
-f h264 -i pipe:0 -c:v copy -f rtsp -rtsp_transport tcp <url>
```

Removes double-encode → ~1 frame latency cut, large CPU drop, quality ceiling = scrcpy encoder.

**Drive quality from a tier**, not hardcoded scrcpy args. Add `max_size` (this unlocks resolution → 1080/1440).

| Tier | max_size | bit_rate | max_fps |
|------|----------|----------|---------|
| 480  | 480      | 2M       | 30      |
| 720  | 720      | 4M       | 30      |
| 1080 | 1080     | 8M       | 60      |
| 1440 | 1440     | 12M      | 60      |

Tier change = restart that scrcpy session with new args. scrcpy `i-frame-interval=1` (fast keyframes for switch; see Section 3).

GOP is now scrcpy's (`i-frame-interval`), not ffmpeg's `-g`.

## Section 2 — Adaptive quality (WebRTC stats-driven)

**Client loop** (~5s while WebRTC active): `_pc.getStats()`, extract from `inbound-rtp` + `candidate-pair`:
- packet loss = `packetsLost / (packetsReceived + packetsLost)`
- RTT = `currentRoundTripTime`
- `framesDropped`

**Hysteresis (no flapping):**
- step **down** one tier if: loss > 3% OR RTT > 250ms
- step **up** one tier if: loss < 1% AND RTT < 120ms sustained ≥15s
- clamp 480↔1440, cooldown 10s after any change
- startup tier = 720

**Apply:** client POSTs `/instances/{serial}/quality {tier}`. Server sets tier on the `ScrcpySession`, restarts scrcpy. Existing WebRTC PC survives; only upstream RTSP source blips ~1s.

**Manual override:** same endpoint; manual pick disables auto for 60s (user intent wins).

Only auto-*downgrade* fires readily mid-play; auto-*upgrade* debounced 15s so restarts are rare.

## Section 3 — Fast instance switch (server-side mux path, 3a)

**mediamtx serves one extra path `active`.** Server re-points `active`'s source to the selected instance's RTSP on switch.

- Browser connects to `active` **once**, never renegotiates. Switch = server repoint only.
- Sub-100ms + one keyframe (scrcpy `i-frame-interval=1` makes keyframe arrive fast). No re-ICE.

**Open unknown (spike first):** verify mediamtx can re-point a path source at runtime — via `runOnDemand`/source edit + config reload, or the mediamtx HTTP API (`api: yes`). If mediamtx won't repoint live, fall back to **3b**: reuse one PC, ICE-restart renegotiate (~300-500ms) instead of full teardown.

## Non-goals (YAGNI)

- Audio streaming.
- TURN relay (STUN-bound-to-Tailscale already solves NAT; see docs/TROUBLESHOOTING.md).
- Multi-viewer per instance.
- Changing the input transport (already fast).
