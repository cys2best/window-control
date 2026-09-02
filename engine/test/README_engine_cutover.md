# Final engine direct-cutover verification

This is the final Windows/device acceptance gate for the engine-only runtime.
It is not a macOS smoke test. A PASS requires a Windows Host PC, exactly five
ready ADB devices, a real local browser, the configured public VPS, a real
mobile device, the produced installer, and the complete eight-hour soak.

## Required environment

Set these only in the verifier terminal. The runner passes one sanitized copy
to the app, never puts capability values in URLs or durable evidence, and
redacts bounded diagnostics:

```powershell
$env:AUTH_TOKEN = "<mobile-and-browser-login-secret>"
$env:TUNNEL_SECRET = "<PC-to-VPS-tunnel-secret>"
$env:ENGINE_SIGNALING_SECRET = "<must-match-the-VPS-JWT-secret>"
$env:TURN_CREDENTIAL = "<TURN-credential>"
$env:TURN_HOST = "<TURN-host>"
$env:TURN_USERNAME = "<TURN-user>"
$env:PUBLIC_UI_URL = "https://window-control.example.com"
```

Do not paste these values into prompts, terminal transcripts, URLs, or issue
reports. Before starting, close every existing WindowControl source app,
`engine.exe`, and browser left by an earlier cutover run. The verifier refuses
to attach to processes it did not start.

## Run

First build the engine and run the complete unfiltered C++ suite behind the
real Node relay, using the same sequence as the `build-engine` CI job (no
`--gtest_filter`):

```powershell
cmake --build engine\build --config Release
$env:JWT_SECRET = ""
$relay = Start-Process node -ArgumentList "server.js" `
  -WorkingDirectory "infra\vps\signaling" -PassThru
try {
  engine\build\Release\engine_tests.exe
  if ($LASTEXITCODE -ne 0) { throw "engine_tests.exe failed" }
} finally {
  Stop-Process -Id $relay.Id -ErrorAction SilentlyContinue
  Wait-Process -Id $relay.Id -ErrorAction SilentlyContinue
}
```

Then, from the repository root on the Windows Host PC:

```powershell
.\engine\verify-engine-cutover.ps1 `
  -Serials emulator-5554,emulator-5556,emulator-5558,emulator-5560,emulator-5562 `
  -PerformanceEvidenceDir engine\test\performance-approved `
  -PublicSignalingUrl wss://signal.example.com `
  -FilePrompts -SoakHours 8
```

The wrapper creates a unique
`engine/test/engine-cutover-<timestamp>-<pid>-<nonce>/` evidence directory.
With `-FilePrompts`, answer the one live prompt from a second terminal:

```powershell
.\engine\verify-engine-cutover.ps1 -Confirm PASS
# or
.\engine\verify-engine-cutover.ps1 -Confirm FAIL
```

Confirmation is accepted only when nonce, verifier PID, and process start time
all match. A response is consumed once. Zero, stale, reused-PID, or ambiguous
live prompts are rejected.

## Recorded performance ruling

The owner explicitly authorized:

> OVERRIDE CUTOVER: skip five-instance validation; proceed with engine-only cutover

The wrapper therefore passes that exact recorded ruling. `result.json` labels
the performance checkpoint `OVERRIDDEN`; it never calls the omitted comparison
a measured PASS. `-PerformanceEvidenceDir` remains in the interface so a later
run can be audited against the original evidence location, but the current
recorded override supersedes the absent four-result hash artifact.

This override waives only the legacy-versus-engine performance comparison. It
does not waive Windows C++ tests, local/public/mobile behavior, installer and
firewall checks, the eight-hour soak, or tray-exit cleanup.

## Operator checkpoints

Type PASS only after every printed item is physically observed. The runner
also validates the machine-readable invariants:

1. Production `/instances/{id}/select` is used and no staging input/media
   route, legacy process, dependency, or asset remains.
2. Two independent production local pages show video and DataChannel drag /
   proportional scroll; `/admin/health` reaches two local peers. For each
   close, the verifier awaits the production session close path, observes the
   successful WHEP `DELETE`, polls the expected peer count within the
   handshake timeout, and only then terminates its owned browser helper.
3. The public production UI uses the configured VPS and exact viewer-token
   query authentication. This is checked from the browser's CDP-observed
   WebSocket URL against the exact session/role/token query; capability values
   are never persisted. A real mobile device uses bearer auth for selection
   and WHEP and confirms video/input.
4. The local/public race leaves exactly one winner. Twenty rapid switches reap
   every abandoned peer within the handshake timeout.
5. `480 -> 720 -> 1080 -> 1440 -> 480` advances generation and matching
   engine/browser decoded dimensions, including the return to the initial 480
   dimensions, without replacing the CDP-observed WHEP `Location` resource or
   RTCPeerConnection identity.
6. Killing only the selected scrcpy server advances generation while keeping
   the engine PID, WHEP port, and peer. Killing only the PID/start-time-scoped
   owned engine produces a new PID, dynamic WHEP URL/token, fresh selection,
   and client reconnection. Every engine is registered as an exact
   PID/create-time member of the owned app process tree before any recovery
   kill; a missing, reused, replaced, or ambiguous identity fails closed.
7. The eight-hour five-instance soak records process count, peer count, ADB
   forwards, CPU, RSS, and browser `framesDecoded` on 480 absolute minute
   deadlines. Collection overhead is subtracted from the next wait instead of
   shifting the cadence. Actual elapsed time and each relative sample timestamp
   are validated; a shortened actual run is `INCOMPLETE`, never FAIL or PASS.
8. The produced installer launches its installed executable, owns an engine
   program firewall rule whose path is the installed engine, then uninstalls
   and removes both runtime and rule-owned state. To avoid duplicate ownership
   of the five ADB devices, the verifier first requires tray Exit of the source
   app, and later requires tray Exit of the installed app before uninstall.
9. Exit WindowControl only from its tray. PASS requires zero app processes,
   zero owned engines, and zero instance forwards afterward.

`result.json`, `verification.log`, and `soak-samples.json` are written
incrementally. `-KeepOnFailure` retains the app/engine/device state for
diagnosis; nonce-scoped prompts and verifier-owned browser helpers are still
cleaned in `finally`. Never use Task Manager or broad process-name kills to
"clean up" a failed run—inspect the retained evidence first.

## Results

- `PASS`: every non-performance gate completed, including the real eight-hour
  soak; performance remains separately labeled `OVERRIDDEN`.
- `INCOMPLETE`: any checkpoint was skipped or the soak was shortened.
- `FAIL`: an automatic invariant or operator checkpoint failed. Return to the
  owning task, add a failing regression test, fix it, and repeat every affected
  automated and real-device gate.

The C++ build and this real matrix must be run on Windows. Python tests on
macOS use stubs and fakes and cannot establish Windows, browser, mobile,
installer, firewall, or eight-hour-soak acceptance.
