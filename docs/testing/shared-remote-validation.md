# Shared remote streaming acceptance

The assessor validates measured JSON exported by the client. Its synthetic
pytest fixtures test the assessor only; they are not captured Windows/iPhone
runs and cannot establish connectivity, packaged launch or release acceptance.

The October 9 tested code commit is
`dcd002cfeed651e3dd64eea8624044747f7eb54f`, branch
`feat/direct-webrtc-shared-turn`; its Windows workflow run is `37877480719`
(native tests, runtime gate, staged host smoke and installer all passed).
Actual results and unavailable cases are recorded in
[the exact-SHA matrix](results/shared-remote/dcd002cfeed651e3dd64eea8624044747f7eb54f-matrix.json).
The later result-documentation commit is not the CI-tested code SHA. No physical
iPhone, persistent installed Windows PC or controlled media path was supplied,
so no device or release-ready claim follows from source/build checks.

The staged CI smoke uses copied actual PyInstaller onedir output with Windows
System32 as its child PATH; it does not install `EmuCtrlInstaller.exe`. Current
user DPAPI save/restart uses a local trusted HTTPS registration fixture and
deliberately unavailable WSS; it establishes identity persistence and local API
startup, not public broker pairing, LAN from another device or media capture.
Raw engine downloads require the matching/newer x64 VC runtime; see
[packaging compatibility](remote-engine-compatibility.md).

## Capture and assessment

Use the exact packaged Windows engine and iPhone build being tested. Disable
Tailscale on both endpoints and make no router changes. Record the branch commit,
Windows workflow run and installer/engine artifact IDs, mobile build ID, and
libdatachannel/libnice/backend versions with each result.

1. Independently measure the controlled path's sustained capacity in the media
   direction. Retain the measurement tool/version, endpoint roles, path/profile,
   duration, timestamp and observed throughput intervals in a separate sanitized
   artifact. The path must sustain at least 12 Mbps; record the supported capacity
   value rather than deriving it from WebRTC receive bitrate. Match the network
   conditions and relay path used for the stream. The assessor trusts this numeric
   input; the operator must supply its provenance for acceptance review.
2. Select a landscape 1920×1080 source, the 1080 tier's 8 Mbps encoder target,
   and the expected route intent in the statistics controls. Enter independently
   measured capacity. Intent does not override the actual selected route.
3. Start a fresh capture after sampler interval baselines are established. Retain
   initial cumulative truth: freeze/downgrade counters must begin at zero. Do not
   edit nonzero counters to zero or subtract away a previous downgrade. Use a
   fresh authorized stream/window when necessary. Unknown initial measurements
   cannot be filled with fixture values.
4. Capture at least 600 seconds of continuous evidence. Export the measurement.
   Web downloads `remote-measurement.json`. Native uses the share sheet to share
   JSON text; save or copy that exact text to a UTF-8 JSON file for assessment.
   Changing quality, selected route, operator capacity or expected intent starts
   another window; do not splice separate exports into a purported continuous run.
5. Run from the feature worktree:

   ```bash
   rtk proxy env UV_CACHE_DIR=/private/tmp/remote-peer-uv-cache uv run python scripts/validate_remote_stream.py captured-evidence.json
   ```

   Preserve stdout and the exit status alongside the capture and capacity artifact.
   Exit `0` means the submitted measurements pass; `1` means complete valid
   evidence contains a measured acceptance violation; `2` means missing, invalid
   or inconclusive evidence. Output is sanitized JSON with `status`, `reasons`
   and `metrics`. Invalid observations take precedence over measured violations.

## Exported evidence contract

`RemoteRunEvidence` has root fields `capacity_mbps`, `target_bitrate_mbps`,
`expected_route` and `samples`. Root and every sample target must agree;
conflicting targets are invalid. Expected intent is `direct`, `turn_udp`,
`turn_tcp` or `turn_tls`; exported `unknown` cannot establish acceptance.

| Sample field | Required measurement |
| --- | --- |
| `elapsed_s` | Finite nonnegative seconds, strictly increasing; duration is last minus first |
| `source_width`, `source_height` | Actual selected source dimensions, both integers |
| `decoded_width`, `decoded_height` | Independently observed decoded dimensions, both integers |
| `decoded_fps` | Actual decoded frame delta over the stats interval |
| `target_bitrate_mbps` | Actual configured/requested encoder tier target, separate from received traffic |
| `bitrate_mbps` | Optional/null diagnostic receive bitrate; no constant-motion floor |
| `adaptive_downgrades` | Cumulative nonnegative integer automatic downgrade count; manual pins excluded |
| `total_freeze_s` | Actual cumulative freeze seconds, beginning at zero and never decreasing |
| `max_freeze_s` | Actual cumulative longest observed freeze, beginning at zero and never decreasing |
| `route` | Actual selected candidate pair: `direct` or `relay` |
| `address_family` | Actual selected evidence: `IPv4`, `IPv6` or `mixed` |
| `relay_protocol` | Actual selected relay protocol: `udp`, `tcp` or `tls`; `unknown` allowed only for direct |

Missing/null required fields, invalid numbers (including NaN/infinity, booleans
and negative values), unknown selected evidence, duplicate/out-of-order timestamps,
counter decreases and nonzero initial cumulative counters are inconclusive.
Dimensions cannot be derived from tier names. Route and relay protocol cannot be
inferred from WSS, configured TURN URLs or an unselected candidate.

The current exporter samples every one second and has no cadence metadata. A gap
over two seconds is inconclusive; exactly two seconds is tolerated. A slower
customized sampler cannot establish completeness with this contract. Short valid
captures fail the duration criterion. Receive bitrate may be zero or null on
static content without failing an otherwise complete run.

Every sample must retain source and decoded dimensions of 1920×1080, at least
30 decoded FPS, target exactly 8 Mbps, and zero adaptive downgrades. Capacity must
be at least 12 Mbps and duration at least 600 seconds. Longest freeze must be
at most one second (equality passes). Cumulative freeze increase divided by
duration must be strictly below 0.01 (6/600 seconds fails). Every selected route
must match intent; TURN additionally requires the corresponding selected relay
protocol. Address-family results must match the separately recorded matrix case.

Native stats without a longest-freeze observation export `max_freeze_s: null`.
Those runs remain inconclusive even while frames arrive. One-second FPS buckets
cannot prove the longest-freeze criterion; obtain an actual observation rather
than inventing zeros. This assessor does not supply missing native telemetry.

## Required packaged-device matrix

Record each required case as `pass`, `fail` or `untested`, with a concrete reason.
Use sanitized result artifacts under `docs/testing/results/shared-remote/`.
Each result identifies the exact tested SHA, artifact/build IDs and backend
versions, selected route/family/protocol, capacity artifact, captured media file,
assessor output/exit status and other timing evidence. Missing devices, test
service or controlled network profiles leave affected rows untested.

| Media profile | IPv4 | IPv6 | Mixed-family reachability |
| --- | --- | --- | --- |
| Direct selected path | Required | Required | Record actual topology; mixed-family relay is covered below |
| TURN/UDP selected relay | Required | Required | Required |
| TURN/TCP, endpoint UDP blocked | Required | Required | Required |
| TURN/TLS on port 443, endpoint UDP blocked | Required | Required | Required |

For every required sustained-media profile, preserve a real ten-minute capture
and independent capacity evidence, with assessor exit 0. Confirm direct media
has no continuous TURN payload; allocation/connectivity checks alone do not
establish relay media. TURN cases require actual selected protocol observations.

Also record the following independent device checks; the media assessor does not
validate them:

- Fresh packaged launch without a development vcpkg PATH, runtime DLL closure,
  native engine tests, current-user DPAPI identity persistence/restart, and LAN
  availability when the remote service is absent.
- Owner QR/link plus six-digit pairing and reconnect with the saved token; two
  concurrent independent PC installations remain isolated. Wrong/revoked tokens,
  cross-installation sessions, pairing replay, stale revisions and arbitrary
  forwarding must reject.
- Production-length TURN credentials expire at 3600 seconds, with controlled
  renewal at 3300 seconds; expired credentials cannot create a new allocation.
- Revocation stops input/media within five seconds while the PC is connected.
  Service outage retains working media for the original bounded 60-second grace.
- Broker restart, PC offline, unavailable TURN, negotiation cancellation, network
  switch and LAN during service outage; record observed recovery or failure.

If native IPv6 media fails, collect actual RTP/SRTP packet sizes,
fragmentation/DF and path-MTU evidence before a focused fix and rebuild. Retain
IPv6 coverage. A source-suite pass or successful installer build cannot replace
these device observations or mark the branch release-ready.

Captured artifacts and assessor output must omit private addresses, raw
candidates, tokens, pairing secrets, TURN credentials and SDP. The assessor
emits fixed reasons and whitelisted aggregate metrics rather than echoing input
text or file errors. Store separately sanitized capacity and timing evidence.

One native failure is unresolved. `InputRouter.EchoIsReflectedVerbatimOnSamePeer`
ended the Windows test process with access violation `0xC0000005` in 2 of 10
suite executions on this branch (run `37288341569`, and the staged step of run
`37874798974`). The same binary passed in the other executions, including both
executions of the tested commit. The fault occurs outside the test thread and
its cause is not established. The test executable now prints the faulting
thread, address and symbolized frames (`engine/test/crash_trace.cpp`); read the
`[crash]` lines of the next failed run before changing engine or fixture code.
