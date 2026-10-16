# Full Migration to the C++ Engine — Design

## Motivation

The project's prior planning (`docs/scrcpy-whep-optimization-spec.md`)
filed a native C++ WebRTC engine as "Phase 4 — Deferred": legitimate at
N≥6 instances and to be reassessed only after Phase 0 measurements. Current
usage is 5 concurrent LDPlayer instances, which is below that literal
threshold. The project owner has explicitly authorized designing the
migration now because usage is close to the earlier heuristic and the C++
prototype has since been build- and device-verified. That authorization is
not itself evidence of an efficiency win: the migration still has a
before/after performance gate at 5 instances before cutover.

This spec designs replacing mediamtx, the in-process aiortc WHEP server
(`webrtc_manager.py`/`whep_app.py`), the ffmpeg copy-mux path, and the
public-path Python bridge (`signaling_bridge.py`) with the C++ engine as the
sole media+signaling path for every instance.

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
signaling protocol. The browser client (`src/client/app.js`) speaks both
local WHEP and the VPS signaling relay as `role=viewer`. Both browser paths
make the viewer the offerer and exchange one fully-gathered SDP offer/answer;
the public path sends raw SDP text through the relay. The current C++ engine
does **not** match that protocol: it makes the engine the offerer, wraps SDP
and trickle candidates in JSON, and creates its DataChannel from that side.
The migration standardizes both local and public negotiation on the existing
viewer-offerer shape instead of treating the prototype protocol as compatible.

## Goals

- Replace ffmpeg, mediamtx, aiortc, and `signaling_bridge.py` with
  `engine.exe` as the only media/signaling path, for both local (WHEP) and
  public (VPS-relayed) viewers.
- Preserve input working via WebRTC DataChannel (accepting the "requires
  an active session" regression — see Non-goals).
- Preserve the current ability to create multiple local WHEP sessions and to
  negotiate local and public candidates concurrently during the browser's
  connection race. The VPS relay still permits only one public viewer role per
  instance at a time.
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
- **No live login/user-auth system.** FastAPI's existing `AUTH_TOKEN` login
  and signed cookie remain the user-facing gate. The raw `AUTH_TOKEN` is never
  passed to `engine.exe`. Instead, an authenticated `/select` response carries
  a short-lived, instance-scoped WHEP capability minted by Python and validated
  by the engine. This is still shared-secret placeholder auth, not a user system.
- **No seamless scrcpy-crash recovery for every failure mode.** A consumed
  scrcpy video/control socket pair is one-shot, so the engine does not pretend
  it can recover by reconnecting to the dead server. Python relaunches
  scrcpy-server and issues a generation-checked reconnect command. This is
  visible as a brief freeze, not a dropped WebRTC session, but is not instant.
- **Single process per instance, not a shared multi-instance process.**
  Deliberately rejected in favor of crash isolation and C++ simplicity —
  see "Process model" below.

## Architecture

### Division of responsibility

**Python (`app.py` / `InstanceManager` / `ScrcpySession`) retains:**
- LDPlayer instance discovery.
- ADB/scrcpy-server process launch, termination, and port-forward setup.
  `engine.exe` still has no ADB knowledge; it connects to a forwarded TCP
  port only after Python has launched the matching scrcpy generation.
- Quality-tier decisions (`POST /instances/{id}/quality` stays the
  client-facing contract).
- Process supervision: spawns one `engine.exe` per discovered instance,
  watches the engine subprocess, polls its loopback health endpoint for
  source disconnect/stall state, and drives scrcpy relaunch/reconnect. An ADB
  shell process handle may provide an additional signal, but engine-observed
  socket/frame health is authoritative because the remote Android process is
  not reliably represented by a local `Popen` lifetime.
- Engine readiness and endpoint discovery. Each engine binds dynamic WHEP and
  admin ports, emits one structured ready record on stdout, and is not exposed
  through `/select` until Python has received that record.
- Remaining HTTP routes: `/instances`, `/select` (now returns a WHEP URL
  pointing at that instance's `engine.exe` port instead of Python's own
  `WHEP_PORT`), `/preview`, `/keyframe`, `/quality` — all still behind the
  existing `AUTH_TOKEN` authentication gate (browser cookie or native bearer).
- Capability minting: Python holds a distinct per-launch engine HMAC secret,
  passes it by environment (not command-line), and signs short-lived WHEP
  capabilities scoped to one instance. If VPS JWT auth is enabled, Python
  also mints session-scoped engine/viewer signaling tokens from a separate
  signaling secret.

**`engine.exe` (one persistent process per instance, spawned at discovery
time — not lazily on first viewer) owns:**
- The scrcpy video+control TCP socket, connected persistently, decoupled
  from whether any WebRTC peer is currently attached (0-viewer-safe, same
  intent as today's Python persistent-loop).
- Two embedded HTTP listeners using `cpp-httplib` via vcpkg:
  - An externally bound WHEP listener serving `POST /whep` and
    `DELETE /whep/{session_id}`. POST validates the instance-scoped bearer
    capability when auth is enabled; DELETE uses an unguessable session
    resource identifier returned in `Location`.
  - A separately bound loopback-only admin listener serving
    `GET /admin/health`, `POST /admin/reconnect`, and
    `POST /admin/keyframe`. It is not a route on the externally bound WHEP
    listener; loopback binding is the security boundary.
- A VPS signaling connection (`role=engine`, `session=<instance_name>`),
  maintained concurrently with local WHEP. The signaling transport remains
  persistent, but peer connections are created per viewer offer rather than
  reusing the prototype's single offerer peer.
- A peer registry: zero or more local WHEP peers plus at most one current
  public peer. Every peer has independent RTP/RTCP/DataChannel state and is
  reaped on DELETE, failed/disconnected state, or handshake timeout.
- The viewer-created reliable, ordered `"input"` DataChannel for each peer,
  wired directly to this process's scrcpy control client.
- One source-global `SpsPpsCache` observed before fan-out. It is reset on a
  scrcpy generation change and prepends the current SPS/PPS to IDRs for every
  peer, including a peer created after startup configuration passed.

### Negotiation and peer lifecycle

The viewer is the offerer on both transports:

- **Local:** the browser/mobile client creates its `"input"` DataChannel and
  recv-only video transceiver, gathers ICE, then sends the complete SDP offer
  to `POST /whep`. The engine creates a new peer, installs the remote offer,
  adds the H264 send-only track, gathers its own ICE, and returns the complete
  SDP answer plus an unguessable WHEP resource URL.
- **Public:** the browser follows the same offer construction, then sends raw
  SDP over its `role=viewer` VPS WebSocket. The persistent engine signaling
  connection receives the raw offer, creates/replaces the public peer, and
  returns raw SDP answer text. The migration does not introduce the prototype's
  JSON offer/candidate envelope or engine-offerer direction.

Both paths are non-trickle for this migration. Each offer and answer carries
its complete candidate list. The browser's existing local/public race can
therefore create temporary peers on both paths; they are independently
reapable without changing another peer or the source connection.

Local peers use host/Tailscale-oriented ICE configuration; the public peer uses
the configured public STUN/TURN set. Python passes both structured ICE configs
to the engine by environment or protected startup input, never by a loggable
command-line credential. The engine selects the config by transport when it
constructs each answering peer.

Because the viewer is always the offerer, the VPS relay no longer queues an
engine answer for a future viewer. Messages are relayed only while both roles
for that connection are present; an offer sent while the engine is offline is
dropped and the viewer retries/falls back. This prevents an answer from an
abandoned negotiation being delivered to the next viewer.

The scrcpy read loop sends each access unit through the source-global H264
configuration cache once, then fans the selected buffer out to a snapshot of
currently connected peer sessions. Peer creation/deletion and source fan-out
are synchronized without holding the registry lock across network sends.
Each peer keeps its own SSRC/RTP clock; a source generation change never resets
an established peer's RTP timestamp. A failed or backpressured peer is removed
without blocking source reads or another peer. Local WHEP sessions are bounded
by a configurable limit (default 4) and excess POSTs receive 503.

### Process model: one engine.exe per instance

5 concurrent simulators → 5 `engine.exe` processes, each independently
crash-isolated. Explicitly rejected: a single multi-instance-aware
`engine.exe` managing all instances in one process — this was considered
and rejected because (a) it reintroduces most of the orchestration
complexity the hybrid split was chosen specifically to avoid, and (b) it
loses per-instance crash isolation, a property the current design (and
Python's existing per-instance watchdog model) depends on. The intended
saving is removal of per-instance Python media-thread/queue work, ffmpeg,
aiortc, and the shared mediamtx process, not elimination of OS processes by
itself. That saving remains a measurement hypothesis until the 5-instance
acceptance comparison passes.

### Reconnect / quality-tier mechanism

One mechanism serves both on-demand quality changes and crash recovery:

1. **Quality-tier change** (`POST /instances/{id}/quality`, client-facing,
   unchanged): Python serializes the change per instance, terminates the old
   scrcpy-server, launches a new generation with the requested tier, restores
   the ADB forward, then calls the instance's loopback admin endpoint with
   `{scrcpy_port, tier, generation}`. The engine rejects stale generations,
   stops and joins the old source client, connects video then control with a
   bounded readiness retry, reads the new handshake, atomically updates source
   dimensions/tier, resets source-global H264 configuration, and starts the new
   read loop. Fan-out resumes only after fresh SPS and PPS have been observed;
   the engine then requests a fresh IDR and sends it on the same peer registry.
2. **Crash recovery:** a read failure or frame stall changes
   `/admin/health` to `source_state=disconnected|stalled` and freezes existing
   peers. The engine does not autonomously reconnect to the already-consumed
   server socket. Python's watchdog observes the health state, relaunches
   scrcpy-server, and calls the same generation-checked reconnect endpoint.
   Connect retry is used only inside that commanded reconnect to wait for the
   newly launched server to become ready.
3. If `engine.exe` itself exits unexpectedly, its consumed scrcpy sockets are
   no longer reusable. Python terminates/relaunches scrcpy-server as a new
   generation, then spawns a fresh engine against it. This is the one case
   where active peers and the dynamic WHEP endpoint are lost; clients must
   re-run `/select` and negotiate from zero. Acceptable: this is the
   least-common failure mode (the process itself crashing, not its source
   socket).

Keeping the peer connection through a quality change is an acceptance target,
not an assumed WebRTC guarantee. Every supported tier transition must prove
that its H264 profile/level, SPS/PPS change, timestamp continuity, and decoder
behavior work without renegotiation. If any supported browser/mobile client
cannot decode a transition, cutover stops for an explicit choice: cap the tier
set or permit renegotiation. It must not silently ship a black-screen tier.

### Input

Moves from `app.py`'s `/input` WebSocket onto one reliable, ordered WebRTC
DataChannel named `"input"`. The viewer creates it before producing either
its WHEP or VPS offer; the answerer engine accepts it through its DataChannel
callback.

The canonical JSON protocol is the existing browser/mobile protocol, not the
prototype engine schema:

- `click`, `drag_start`, `drag_move`, `drag_end`, and `scroll` use normalized
  `x`/`y`; the engine preserves DOWN/MOVE/UP pairing and scroll cancellation.
- `key` carries the existing string key name; the current Python
  `_JS_KEY_TO_KEYCODE` table moves to a shared client contract/C++ mapping.
- `idr` requests a source IDR with the existing per-instance rate limit.
- `echo` is reflected on the same DataChannel for the current RTT UI.

The engine uses dimensions from the latest scrcpy handshake when converting
normalized coordinates. It calls its own control client directly—no Python
relay or shared `ScrcpyControl` object. `app.py`'s `/input` route is deleted.
Finger/drag state is per peer. If a peer disconnects while its finger is down,
the engine sends a best-effort UP before discarding that peer's state so one
viewer cannot leave input stuck for the next.

### Endpoint discovery, authentication, and network binding

Each engine asks the OS for an available externally bound WHEP TCP port and an
available loopback-only admin TCP port. After both listeners are bound and the
initial source handshake succeeds, it writes one machine-readable ready record
to stdout containing the instance name, process id, WHEP port, admin port,
source generation, and dimensions. Python consumes this record with a bounded
startup deadline and stores the actual ports in the tracked instance. This
avoids deterministic-port collisions and makes `/select` return only endpoints
that are genuinely listening.

The Windows installer creates an inbound program rule for the shipped
`engine.exe`; the engine is responsible for using libdatachannel candidates
that are reachable on LAN/Tailscale and through configured TURN. The loopback
admin listener remains unreachable remotely despite the program rule. Port and
candidate behavior is part of the Windows acceptance matrix, not inferred from
the successful single-peer prototype.

Authentication has three distinct boundaries:

1. **FastAPI control API:** the browser continues using the signed
   `wc_session` cookie. The native mobile client may supply the existing shared
   `AUTH_TOKEN` as a bearer credential to FastAPI; it stores that token in
   platform secure storage. This remains shared-token auth, not a user system.
2. **WHEP:** authenticated FastAPI selection returns a short-lived
   `whep_token` signed with the per-launch engine secret and scoped to instance,
   operation, and expiry. Browser/mobile send it as `Authorization: Bearer` on
   WHEP POST. The engine never receives the raw `AUTH_TOKEN`. WHEP CORS allows
   the Authorization header and exposes `Location`; it does not rely on
   credentialed cross-origin cookies. The returned session resource id is
   cryptographically unguessable and acts as the DELETE capability.
3. **VPS signaling:** signaling JWT auth is separate from both tokens. A public
   deployment configures a shared signaling JWT secret on Python and the VPS;
   Python mints session-, role-, and expiry-scoped engine/viewer tokens. The
   engine token is supplied by environment and the viewer token is returned by
   `/select`. JWT-disabled signaling is permitted only for trusted development,
   not an internet-exposed deployment.

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
- `src/server/publish_hook.py`, `scripts/test_bridge_manual.py`, and tests that
  exist only for mediamtx/ffmpeg/aiortc/signaling-bridge behavior.
- `WEBRTC_BACKEND` config flag (`config.py`) — no longer a choice.
- `MEDIAMTX_*`, the single Python `WHEP_PORT`, aiortc-only codec configuration,
  and obsolete mediamtx/ICE-port settings from `config.py`.
- `imageio-ffmpeg` and `aiortc` from Python dependencies, together with their
  PyInstaller hidden imports and now-unused transitive packaging entries.
- mediamtx download/staging from `download_assets.py` and Windows CI. Scrcpy and
  the engine artifact remain bundled.

### Client changes required

- **Mobile** (`mobile/src/api/client.ts`, `mobile/src/webrtc/whep.ts`,
  `Stream.tsx`, server setup/context): `/select` now returns the dynamic WHEP
  URL and capability. The client creates `"input"` before its WHEP offer,
  includes the capability on POST, reuses the returned resource URL on DELETE,
  and sends the existing input JSON over the channel. Optional FastAPI bearer
  auth is stored in platform secure storage; the separate `/input` WebSocket is
  removed.
- **Browser** (`src/client/app.js`): the local path makes the same WHEP and
  DataChannel changes. The public path keeps its current viewer-offerer/raw-SDP
  direction, adds the session JWT to the WebSocket URL, creates `"input"`
  before the offer, and preserves the signaling socket until negotiation no
  longer needs it. No prototype JSON signaling envelope is introduced.
- **Both:** `POST /instances/{id}/select` returns `whep_url`, `whep_token`,
  `signaling_url`, `signaling_token`, `ice_servers`, source dimensions, and the
  instance identity. A disabled feature is represented by a null URL/token,
  not a half-configured endpoint.

## Required C++ engine design changes

- Split the prototype's single `WebRtcPeer` into a source owner, a peer/session
  object that can answer an offer, and a synchronized registry/fan-out layer.
- Implement non-trickle WHEP POST/DELETE, handshake timeout, failed-peer reaping,
  CORS, capability validation, and separate external/loopback listeners.
- Change VPS handling from engine-offerer JSON/trickle messages to persistent
  engine transport receiving viewer raw offers and returning raw answers.
- Make scrcpy video/control clients replaceable as one generation, with bounded
  commanded-connect readiness retry, read/stall health, joined teardown,
  dimension refresh, and control-send error detection.
- Move SPS/PPS recovery to the source generation before peer fan-out and reset
  it on reconnect.
- Port the complete canonical input protocol, including finger state, scroll,
  key mapping, IDR throttling, and echo, to viewer-created DataChannels.
- Emit a structured ready record and structured lifecycle logs suitable for
  Python supervision and packaged Windows diagnosis.

## Risks / known technical debt carried into this migration

- **`websocketpp` fragility** (pinned old vcpkg baseline, removed from
  vcpkg's default registry, documented MSVC `/std:c++20` incompatibility
  risk) — becomes more load-bearing under this migration, since VPS
  signaling is now a primary production path for every public session,
  not just a manual-testing tool. The dependency must be pinned reproducibly
  and exercised in Windows CI. Replacing it is allowed only if that can be done
  without changing the raw-SDP protocol defined here.
- **H264 tier compatibility** — source restarts may change SPS/PPS,
  profile/level, resolution, and frame rate. Seamless peer preservation is
  gated by the tier-transition matrix; it is not guaranteed by the design.
- **ICE and Windows firewall behavior** — dynamic HTTP ports and per-peer
  libdatachannel sockets differ from mediamtx's fixed mux ports. Installer
  program rules and real LAN/Tailscale/TURN candidate checks are mandatory.
- **Peer churn and backpressure** — local/public race probes, rapid instance
  switching, and abandoned WHEP POSTs can create short-lived peers. Registry
  limits, handshake deadlines, disconnect reaping, and per-peer send-failure
  isolation prevent one peer from stalling the source or leaking indefinitely.
- **Direct application cutover** — there is no runtime backend flag or dual
  media stack. Operational rollback is the last known-good installer/release;
  legacy deletion occurs only after the new path passes every gate below.

## Verification and cutover gates

The migration is not complete merely because `engine.exe` builds. Cutover
requires all of the following evidence:

- **Automated C++:** offer-answer peer tests, WHEP session lifecycle, global
  SPS/PPS fan-out to late peers, canonical input parsing/state, generation-
  checked reconnect, control-send failure, and peer reaping.
- **Automated signaling:** Windows CI launches the repository's local Node
  signaling server and runs `SignalingClient.*` instead of excluding it. Relay
  tests cover raw SDP direction, sequential viewer replacement, role/session/
  expiry JWT claims, reconnect, offline-target drop, and absence of stale SDP
  delivery to a later viewer.
- **Automated Python:** fake engine/scrcpy processes cover ready-record timeout,
  dynamic endpoint publication, engine crash/respawn, source health recovery,
  stale reconnect generations, device removal during recovery, capability
  signing/expiry, and complete dependency/asset cleanup.
- **Client contract:** browser and mobile tests cover DataChannel creation before
  the offer, all input message types, bearer WHEP POST, resource DELETE, and
  local/public fallback without the `/input` WebSocket.
- **Windows device matrix:** local browser WHEP, public browser via VPS relay,
  mobile WHEP, simultaneous local/public race, rapid switching, every quality
  tier transition, scrcpy crash with peer preservation, engine crash with
  client reconnect, and 5-instance overnight soak with no process/socket/peer
  leaks.
- **Performance gate at 5 instances:** capture the prior spec's metrics for the
  legacy and engine builds under identical no-viewer and one-viewer workloads:
  host CPU, memory, per-instance bitrate, glass-to-glass latency, warm/cold
  switch latency, and jitter-buffer delay. The owner reviews these numbers
  before legacy deletion; a regression requires an explicit override.

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
- Python mints short-lived WHEP capabilities; the raw `AUTH_TOKEN` never enters
  `engine.exe`. Browser cookie auth and native FastAPI bearer auth remain
  shared-token placeholders for a future login system.
- `signaling_bridge.py` is retired; `engine.exe` dials the VPS directly.
- Viewer-offerer, fully gathered, raw-SDP negotiation is shared by WHEP and VPS;
  the prototype's engine-offerer JSON/trickle protocol is retired.
- One peer registry supports multiple local WHEP sessions and one public peer;
  H264 configuration state belongs to the source generation before fan-out.
- Rollout is a direct cutover — no `WEBRTC_BACKEND` flag, no coexistence
  period — with the previous installer retained as operational rollback.
- Quality-tier changes and crash recovery share one mechanism
  (`/admin/reconnect`) on a separate loopback listener. Python owns scrcpy
  relaunch; the engine preserves peers and accepts only newer generations.
- Engine WHEP/admin ports are dynamically allocated and reported through a
  structured readiness record; Windows uses an engine program firewall rule.
- One `engine.exe` process per instance, not a shared multi-instance
  process — crash isolation and C++ simplicity outweigh the smaller
  process-count reduction a shared process would offer.
