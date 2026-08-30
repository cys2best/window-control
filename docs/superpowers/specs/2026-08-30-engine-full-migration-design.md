# Full Migration to the C++ Engine — Design

## Motivation

The project's own prior planning (`docs/scrcpy-whep-optimization-spec.md`)
filed a native C++ WebRTC engine as "Phase 4 — Deferred, reassess only once
instance-count economics justify it (N≥6 instances)." Current/near-term
usage is at 5 concurrent LDPlayer instances, at that threshold. This spec
designs the migration: replace mediamtx and the in-process aiortc WHEP
server (`webrtc_manager.py`/`whep_app.py`), the ffmpeg copy-mux path, and
the public-path Python bridge (`signaling_bridge.py`) with the existing
(now build-verified — see `2026-08-29-cpp-engine-sps-pps-cache.md`) C++
engine, as the sole media+signaling path for every instance.

## Current state (context for implementers)

Three parallel Python/C++ implementations of "scrcpy H.264 → WebRTC" exist
today:

1. **mediamtx + ffmpeg copy-mux** (`mediamtx_manager.py`, `scrcpy_session.py`
   `build_ffmpeg_args()`/`start_video()`) — default path.
2. **In-process aiortc WHEP server** (`webrtc_manager.py`, `whep_app.py`,
   `scrcpy_session.py` `start_video_aiortc()`) — opt-in via
   `WEBRTC_BACKEND=aiortc`, itself a partially-completed prior migration
   (Phases 2/3 of `2026-08-28-mediamtx-aiortc-migration.md` were never
   started).
3. **Standalone `rtc_engine.py` CLI** — a Python analogue of `engine.exe`,
   speaking the VPS signaling protocol directly. Not wired into
   `app.py`/`InstanceManager`; manual-testing tool only.

`engine/` (the C++ engine) currently only reimplements the leaf that
`rtc_engine.py` reimplements: one scrcpy socket → one libdatachannel peer,
signaling only over the VPS relay protocol. It has no WHEP server, no
multi-instance orchestration, no quality tiers, no auth, no watchdog/
reconnect, and depends on a fragile, semi-abandoned `websocketpp` (pinned
old vcpkg baseline, documented MSVC `/std:c++20` incompatibility risk).

Input (taps/keys) today goes over a separate `/input` WebSocket to
`app.py`, entirely independent of whichever video backend is active,
backed by one shared `ScrcpyControl` object per instance that also serves
IDR-heartbeat requests. This is what allows input to keep working even
with zero viewers.

The mobile app (`mobile/`) is WHEP-only — it has no code path for the VPS
signaling protocol. The browser client (`src/client/app.js`) already
speaks both: local WHEP (`initWebRTC`) and the VPS signaling protocol
directly as `role=viewer` (`initWebRTCPublic`), matching what
`signaling_client.cpp` already implements as `role=engine`.

## Goals

- Replace ffmpeg, mediamtx, aiortc, and `signaling_bridge.py` with
  `engine.exe` as the only media/signaling path, for both local (WHEP) and
  public (VPS-relayed) viewers.
- Preserve input working via WebRTC DataChannel (accepting the "requires
  an active session" regression — see Non-goals).
- Preserve on-demand quality-tier changes without a client-visible
  reconnect (no WHEP renegotiation, no ICE restart).
- Fast recovery from a dead/restarted scrcpy-server without dropping an
  active viewer where avoidable.
- Direct cutover — no `WEBRTC_BACKEND` flag, no coexistence period. Legacy
  Python media code is deleted in the same effort.

## Non-goals / accepted regressions

- **Input no longer works with zero viewers.** Today's shared
  `ScrcpyControl` object lets `/input` WS inject taps even with no active
  stream. After migration, input rides the WebRTC DataChannel, so it only
  works while a session is connected. Accepted explicitly by the project
  owner.
- **No live login/user-auth system.** `engine.exe`'s WHEP endpoint checks
  a shared `AUTH_TOKEN` (same posture as today's cookie gate, ported to
  C++). This is a known placeholder, expected to be reworked when a real
  login system is built — no design effort spent here beyond parity with
  today.
- **No seamless scrcpy-crash recovery for every failure mode.** `engine.exe`
  retries the last-known-good scrcpy port itself first; a scrcpy-server
  process crash still requires Python to relaunch scrcpy-server and issue
  a reconnect command, which is visible as a brief freeze, not a dropped
  WebRTC session (see "Reconnect / quality-tier mechanism" below) — but is
  not instant.
- **Single process per instance, not a shared multi-instance process.**
  Deliberately rejected in favor of crash isolation and C++ simplicity —
  see "Process model" below.

## Architecture

### Division of responsibility

**Python (`app.py` / `InstanceManager` / `ScrcpySession`) retains:**
- LDPlayer instance discovery.
- ADB/scrcpy-server process launch and port-forward setup (unchanged from
  today — `engine.exe` still just connects to a forwarded TCP port it's
  told about; it has no ADB knowledge).
- Quality-tier decisions (`POST /instances/{id}/quality` stays the
  client-facing contract).
- Process supervision: spawns one `engine.exe` per discovered instance,
  watches both scrcpy-server and `engine.exe` for unexpected exit, and
  drives the reconnect mechanism when either dies.
- Remaining HTTP routes: `/instances`, `/select` (now returns a WHEP URL
  pointing at that instance's `engine.exe` port instead of Python's own
  `WHEP_PORT`), `/preview`, `/keyframe`, `/quality` — all still behind the
  existing `AUTH_TOKEN` cookie gate.
- `AUTH_TOKEN` generation/config — the same token value is passed to each
  `engine.exe` at launch (CLI arg or env var) for it to check on WHEP
  requests.

**`engine.exe` (one persistent process per instance, spawned at discovery
time — not lazily on first viewer) owns:**
- The scrcpy video+control TCP socket, connected persistently, decoupled
  from whether any WebRTC peer is currently attached (0-viewer-safe, same
  intent as today's Python persistent-loop).
- A local embedded HTTP server (new C++ dependency — `cpp-httplib`, single
  header, added via vcpkg alongside the existing `libdatachannel`/
  `websocketpp` dependencies) serving:
  - `POST /whep` / `DELETE /whep/{session_id}` — the client-facing WHEP
    contract, checked against `AUTH_TOKEN`.
  - `POST /admin/reconnect {scrcpy_port, tier}` — localhost-only, no auth
    token needed (network-boundary-protected: only Python on the same
    host calls this). See "Reconnect / quality-tier mechanism" below.
- A VPS signaling connection (`role=engine`, `session=<instance_name>`),
  maintained concurrently with the local WHEP server using the existing
  `signaling_client.cpp`/`WebRtcPeer` code — both local and public viewers
  are served by the same running process, no mode switch.
- The WebRTC peer's `"input"` DataChannel, wired directly to this
  process's own scrcpy control-socket client (no Python relay).
- The existing `SpsPpsCache` H264 configuration recovery
  (`h264_nalu.h`/`.cpp`, already shipped) — still needed since a fresh
  WHEP viewer can join after the SPS/PPS-carrying startup payload.

### Process model: one engine.exe per instance

5 concurrent simulators → 5 `engine.exe` processes, each independently
crash-isolated. Explicitly rejected: a single multi-instance-aware
`engine.exe` managing all instances in one process — this was considered
and rejected because (a) it reintroduces most of the orchestration
complexity the hybrid split was chosen specifically to avoid, and (b) it
loses per-instance crash isolation, a property the current design (and
Python's existing per-instance watchdog model) depends on. The actual cost
this migration removes at N≥6 is per-instance Python-asyncio overhead +
ffmpeg + mediamtx's own process, not "having multiple OS processes" per
se — one lightweight `engine.exe` per instance already captures that win.

### Reconnect / quality-tier mechanism

One mechanism serves both on-demand quality changes and crash recovery:

1. **Quality-tier change** (`POST /instances/{id}/quality`, client-facing,
   unchanged): Python kills the current scrcpy-server for that instance,
   launches a new one with the new tier's bitrate/resolution args on a
   forwarded port, then calls that instance's `engine.exe` at
   `POST 127.0.0.1:<port>/admin/reconnect {scrcpy_port: <new_port>}`.
   `engine.exe` drops its old scrcpy socket, connects to the new port,
   requests a fresh IDR, and resumes forwarding NALUs on the *same*
   WebRTC peer/DataChannel/WHEP session — no SDP renegotiation, no ICE
   restart (WebRTC tolerates a resolution/bitrate change within an
   established H264 track). Viewer sees a brief freeze, not a dropped
   connection.
2. **Crash recovery**: `engine.exe` first attempts a short local retry
   against the last-known-good scrcpy port itself (handles transient
   TCP-level drops without involving Python at all). If that doesn't
   recover within a bounded number of attempts, Python's watchdog (which
   is separately monitoring scrcpy-server's process liveness) notices,
   relaunches scrcpy-server, and calls the same `/admin/reconnect`
   endpoint with the fresh port — same recovery path as a tier change.
   Exact retry count/backoff timing is an implementation-plan-level
   decision, not fixed here.
3. If `engine.exe` itself exits unexpectedly (not just its scrcpy
   connection), Python's process supervisor respawns it fresh, pointed at
   the current scrcpy port — this is the one case where an active WebRTC
   peer is fully dropped and must reconnect from scratch (WHEP/ICE from
   zero). Acceptable: this the least-common failure mode (the process
   itself crashing, not its socket connection).

### Input

Moves from `app.py`'s `/input` WebSocket onto the WebRTC DataChannel
`engine.exe` already implements (`{"type":"tap"|"swipe"|"key", ...}`,
already matching the wire format `ScrcpyControl` expects). `engine.exe`
calls its own control-socket client directly — no Python relay, no shared
`ScrcpyControl` object across processes. `app.py`'s `/input` route is
deleted.

### What gets deleted

- `src/server/mediamtx_manager.py` and the bundled `assets/mediamtx/`
  binary (no longer downloaded/launched).
- `src/server/webrtc_manager.py`, `src/server/whep_app.py` (the in-process
  aiortc WHEP server and its second uvicorn instance).
- `src/server/rtc_engine.py` (superseded by the now-verified `engine.exe`).
- `src/server/signaling_bridge.py` (public path now handled directly by
  each instance's `engine.exe`).
- The ffmpeg copy-mux code path in `scrcpy_session.py`
  (`build_ffmpeg_args()`, the `-c:v copy` `start_video()` path, the
  `_NaluWriteQueue`/writer-thread machinery that fed it — `engine.exe` now
  owns NALU consumption directly via its own scrcpy socket).
- `ScrcpySession`'s direct ownership of the scrcpy video/control wire
  protocol — it becomes "launch scrcpy-server, launch/supervise
  `engine.exe`," not "speak the scrcpy protocol."
- `app.py`'s `/input` WebSocket route and the
  `/internal/instances/{name}/publish/{start,stop}` mediamtx-hook routes
  (meaningless once mediamtx is gone).
- `WEBRTC_BACKEND` config flag (`config.py`) — no longer a choice.

### Client changes required

- **Mobile** (`mobile/src/api/client.ts`, `mobile/src/webrtc/whep.ts`,
  `Stream.tsx`): WHEP URL now points at the selected instance's
  `engine.exe` port (still returned by `POST /instances/{id}/select`, just
  a different port/host-mapping than today). Input sends over the
  established WHEP `RTCPeerConnection`'s `"input"` DataChannel instead of
  a separate WebSocket to `app.py`.
- **Browser** (`src/client/app.js`): same WHEP-URL change for the local
  path. `initWebRTCPublic()` needs no protocol change — it already speaks
  exactly the VPS signaling protocol `engine.exe` implements as
  `role=engine` — only the input path changes (DataChannel instead of
  `/input` WS).

## New C++ engine work (high-level — implementation plan owns the detail)

- Embedded HTTP server (`cpp-httplib` via vcpkg) implementing WHEP
  (`POST`/`DELETE /whep[/{id}]`) and the local admin endpoint
  (`POST /admin/reconnect`).
- `AUTH_TOKEN` check on WHEP requests (header or cookie, matching
  whatever `app.py`'s existing cookie scheme uses so the value round-trips
  unchanged from browser/mobile).
- Persistent scrcpy connection with reconnect-on-command
  (`/admin/reconnect`) and bounded self-retry-on-drop, replacing the
  current one-shot `ScrcpyVideoClient::Connect()`/`ScrcpyControlClient::
  Connect()` design.
- DataChannel input handling wired to the local `ScrcpyControlClient`
  (already exists — this is largely already-shipped code needing no
  Python relay hookup).
- Concurrent local-WHEP-server + VPS-signaling-client operation in one
  process (both already exist independently; this spec requires them to
  coexist without interfering).

## Risks / known technical debt carried into this migration

- **`websocketpp` fragility** (pinned old vcpkg baseline, removed from
  vcpkg's default registry, documented MSVC `/std:c++20` incompatibility
  risk) — becomes more load-bearing under this migration, since VPS
  signaling is now a primary production path for every public session,
  not just a manual-testing tool. Not addressed by this spec; flagged for
  the implementation plan to decide whether to harden, pin more
  defensively, or evaluate an alternative before or during rollout.
- **No automated CI coverage of the signaling path** — the existing
  `build-engine` GH Actions job explicitly excludes `SignalingClient.*`
  tests (needs a live server). This migration makes that code path
  central; CI coverage remains a gap this spec does not close.
- **No automated multi-instance/process-supervision test** — Python's
  watchdog-over-`engine.exe`-subprocess logic is new code with no existing
  test pattern to copy (today's watchdog supervises ffmpeg/mediamtx, not a
  peer-owning WebRTC process); implementation plan should budget for this.
- Direct cutover means no coexistence/rollback path if a field issue
  surfaces after the legacy code is deleted — accepted per explicit
  decision above.

## Decisions log (from brainstorming)

- Migration shape: hybrid (Python orchestration + per-instance
  `engine.exe`), not a full C++ rewrite of orchestration, not a
  public-path-only migration.
- `engine.exe` owns the scrcpy socket directly (persistent per instance),
  not Python piping NALUs to a leaner C++ leaf.
- WHEP terminates in `engine.exe` itself (embedded HTTP server), not a
  Python WHEP shim relaying to the engine.
- Input moves to the WebRTC DataChannel; `/input` WS is deleted; 0-viewer
  input is explicitly given up.
- `AUTH_TOKEN` check ported into `engine.exe`, explicitly a placeholder
  for a future real login system.
- `signaling_bridge.py` is retired; `engine.exe` dials the VPS directly.
- Rollout is a direct cutover — no `WEBRTC_BACKEND` flag, no coexistence
  period.
- Quality-tier changes and crash recovery share one mechanism
  (`/admin/reconnect`), reusing the WHEP HTTP server rather than a second
  IPC channel.
- One `engine.exe` process per instance, not a shared multi-instance
  process — crash isolation and C++ simplicity outweigh the smaller
  process-count reduction a shared process would offer.
