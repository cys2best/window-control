# Latency Bench Harness — Design

Date: 2026-10-16
Status: Design approved by the user in conversation; spec awaiting review.

## Intent and scope

Before optimizing the streaming pipeline (host-side capture and encode,
intra-refresh, continuous rate control, codec changes), we need numbers. This
harness is a **lab benchmark**: a scripted run that reports tap-to-photon
latency and a per-stage breakdown, so each optimization gets a before/after
comparison. It does not ship to users and adds no always-on telemetry.

Decisions made with the user:

- Receiver: desktop Chrome, driven by Playwright, on a second machine (the
  developer's Mac) reaching the host over Tailscale or LAN.
- Headline metric: **tap-to-photon** — the receiver sends a touch and times
  until the resulting pixel change is displayed. Both timestamps are on the
  receiver clock, so the headline needs no clock sync.
- Source: a **dedicated LDPlayer bench instance** shows a static HTML target
  page (no APK build). The game instance may keep running during a run to
  provide realistic host load.

Assumptions (stated for review):

- Success: one command produces p50/p95/p99 tap-to-photon plus per-stage
  breakdown, and two result files can be diffed.
- Target run-to-run noise at p50: under about 5 ms.
- The bench uses normal pairing. No auth bypass for the stream or the
  DataChannel.

Out of scope: iPhone Safari runs, splitting the guest stage (inject → paint →
capture → encode → adb), network impairment simulation, running the bench in
CI.

## Current state (relevant facts)

- `packages/core/src/webrtc/telemetry.ts` already samples `getStats` (RTT,
  loss, decode, jitter buffer, bitrate). No per-frame or end-to-end timing.
- `engine/src/scrcpy_video.cpp` reads scrcpy frame headers and discards
  `pts_flags`. No stage timestamps anywhere in the engine.
- `engine/src/peer_session.cpp` derives RTP timestamps from wall-clock
  elapsed time, so the engine knows each frame's RTP timestamp at send time.
- `engine/src/input_router.cpp` dispatches DataChannel messages by `type`;
  an `echo` type already exists. `click` sends DOWN then UP immediately.
- `src/server/engine_process.py` passes the host's environment to
  `engine.exe` (positional args are only instance name and scrcpy port).
- `src/server/app.py` `AccessGate`: requests from the LDPlayer guest to
  `10.0.2.2` arrive from loopback but with a non-local `Host`, so they need a
  pairing token unless the path is pairing-exempt (`_is_pairing_exempt`).

## Components

| # | Unit | Location | Responsibility |
|---|------|----------|----------------|
| 1 | Target page | `src/server/bench/target.html` | Full-screen solid colour. Each `pointerdown` toggles black ↔ white. Nothing else rendered. |
| 2 | Host bench switch | `src/server/app.py` | When `EMUCTRL_BENCH=1`: serve `GET /bench/target.html` (pairing-exempt; static, no data) and the `/bench` web page. Otherwise neither route exists. The engine inherits the variable through the child environment. |
| 3 | Engine bench log | `engine/src/bench_log.{h,cpp}` | Enabled only when the engine sees `EMUCTRL_BENCH=1` at startup. Fixed ring buffer (64k entries, preallocated, no allocation on record). Records input and frame events on the host monotonic clock (`steady_clock`, µs). |
| 4 | Engine bench messages | `engine/src/input_router.cpp` | `bench.ping` → reply `{type:"bench.pong", id, engine_us}`. `bench.start` clears the log. `bench.fetch {from, count}` → returns a chunk of entries. All ignored when bench is off. |
| 5 | Clock sync | `packages/core/src/bench/clock.ts` | Ping/pong over the DataChannel; estimates the receiver ↔ engine offset. |
| 6 | Bench page | `apps/web/src/app/bench/` | Connects with the `packages/core` WHEP session, runs the tap protocol, collects per-frame metadata, fetches the engine log, returns a result object to Playwright. |
| 7 | Analysis | `packages/core/src/bench/analyze.ts` | Pure functions: join taps → frames → engine entries, stage durations, percentiles, run validity. |
| 8 | Runner | `scripts/bench/run.mjs` | Launches Playwright Chrome with a pre-paired persistent profile, opens `/bench?n=…&label=…`, waits for the result, writes JSON + Markdown. `--compare a.json b.json` prints per-stage deltas. |

## Data flow

### Setup

1. Operator opens `http://10.0.2.2:<port>/bench/target.html` once in the
   bench LDPlayer instance's browser and selects that instance in the host.
2. Bench page connects. It checks the centre of the video is uniform black or
   white and stable for 1 s; otherwise it aborts with "target page not in
   front".
3. Clock sync: 50 `bench.ping` round trips. Take the sample with the lowest
   RTT: `offset = engine_us − (t_send + t_recv) / 2`. Repeat after the run;
   `drift = |offset_after − offset_before|`.
4. `bench.start`, then 10 warm-up taps (discarded).

### One tap (default N = 200)

1. Page sends `{type:"click", x:0.5, y:0.5, bench_seq}` at `t0`
   (`performance.now()` mapped to ms).
2. Engine records `in_recv` (message received) and `in_inject` (after
   `SendTouch` UP is written), tagged with `bench_seq`.
3. Guest browser toggles colour; scrcpy captures, encodes, sends over adb.
4. Engine records per frame, keyed by RTP timestamp: `f_read` (frame read off
   the scrcpy socket), `f_pkt` (handed to the packetizer), `f_sent` (pacer
   releases the frame's last packet).
5. Page runs `requestVideoFrameCallback` on every frame, draws the frame to an
   8×8 canvas, samples the centre luminance. The first frame after `t0` whose
   luminance crosses the threshold toward the expected colour gives
   `t1 = metadata.expectedDisplayTime`, plus `rtpTimestamp` and
   `receiveTime`.
6. Random 300–700 ms gap, then the next tap (toggle direction alternates).
7. No transition within 2 s → recorded as a **miss**; the page re-reads the
   current colour before continuing.

The page also keeps `rtpTimestamp`, `receiveTime`, `expectedDisplayTime` for
every frame, so engine-video and network stages get thousands of samples.

### Stages

Engine times are converted to the receiver clock with `offset`.

| Stage | From → to |
|---|---|
| input network | `t0` → `in_recv` |
| engine input | `in_recv` → `in_inject` |
| guest (inject + paint + capture + encode + adb) | `in_inject` → `f_read` of the transition frame |
| engine video | `f_read` → `f_sent` |
| network + receive | `f_sent` → `receiveTime` |
| receiver (jitter buffer + decode + display) | `receiveTime` → `t1` |
| **tap-to-photon** | `t0` → `t1` (receiver clock only) |

The transition frame is joined to engine entries by RTP timestamp; the engine
records RTP timestamps in the same units it sends.

## Output

`bench-results/<YYYY-MM-DDTHHMM>-<label>.json` (git-ignored):

- `meta`: git commit, tier, transport (`host`/`srflx`/`relay` from the
  selected candidate pair), offset, drift, N, misses, Chrome version.
- `taps[]`, `frames[]`, `engine[]`: raw samples.
- `stats`: per stage — p50, p95, p99, mean, count.
- `validity`: flags listed below.

A matching `.md` with the stats table. `--compare` prints, per stage, the
p50/p95 delta, and marks a p50 change as significant only when it exceeds the
larger of the two runs' p50 bootstrap 95% interval half-widths.

## Failure handling

| Condition | Effect |
|---|---|
| `drift` > 2 ms | Cross-clock stages marked unreliable; tap-to-photon still valid |
| Miss rate > 5% | Run marked failed |
| ICE restart or transport change mid-run | Run marked failed with reason |
| Engine log wrapped or fetch incomplete | Breakdown omitted; headline still reported |
| Bench messages sent with bench off | Engine ignores them; page times out and reports "engine bench mode off" |

## Security

- Bench mode is off unless `EMUCTRL_BENCH=1` is set on the host. With it off,
  no new routes exist and the engine ignores `bench.*` messages.
- `bench.*` messages travel only on the paired DataChannel.
- `/bench/target.html` is the only pairing-exempt addition; it is static and
  returns no data. It exists only in bench mode.
- `bench.fetch` returns timestamps only — no input coordinates or content.

## Testing

- Vitest, `packages/core/src/bench/`: min-RTT offset and drift; tap → frame →
  engine join by RTP timestamp, including missing entries; percentiles and
  bootstrap interval; threshold detector on synthetic luminance sequences;
  validity flags.
- gtest, `engine/test/`: ring buffer wraparound and chunked fetch; `bench.*`
  ignored when bench is off; `bench.ping` reply shape.
- pytest: `/bench/target.html` and `/bench` absent without `EMUCTRL_BENCH`,
  present and pairing-exempt (target only) with it.
- Manual: one real end-to-end run on LDPlayer + Mac, recorded in the plan's
  verification step.
