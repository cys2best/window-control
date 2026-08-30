# Windows real-device manual E2E gate

Run this gate on the Windows Host PC after the engine builds. It is a manual
acceptance check; this document does **not** claim that Windows verification
has occurred.

## Scope and pass criteria

This checks the local WHEP path against one real Android device or emulator:
scrcpy H.264 capture, browser video, DataChannel touch, independent local
peers, and in-place source reconnect. It passes only when all of these are
recorded:

1. `engine.exe` emits its ready JSON before a browser connects; its WHEP and
   admin ports work.
2. The first browser peer renders changing, non-black video and
   `framesDecoded` is greater than zero and increases.
3. A click in that video causes the expected device-side tap.
4. A second independently opened peer streams without disrupting the first.
5. Reconnecting to a freshly relaunched scrcpy server on a different local
   port, with a strictly newer generation, resumes the *original* tab without
   reload, renegotiation, or ICE restart.
6. The browser(s), engine, static server, and ADB forwards are shut down
   cleanly.

This is a local/dev gate. It does not validate the public signaling path,
authentication, TURN, or a deployed client.

## Prerequisites

- Windows PowerShell, Visual Studio/vcpkg/CMake configured as described in
  [`../BUILD_WINDOWS.md`](../BUILD_WINDOWS.md), and `uv` available.
- A physical Android device (USB debugging authorized) or a running emulator.
  Do not assume `emulator-5554`: select the serial reported by `adb devices`.
- The repository's scrcpy server asset at `src/assets/scrcpy/scrcpy-server`.
  `test.ps1` uses the existing Python `_start_server` helper to push it, start
  scrcpy 3.1, and create the required ADB forward.
- Chrome or Edge on the Host PC. Keep its WebRTC diagnostics open during the
  test. A second browser profile/incognito window is useful for the second
  peer.

All commands below run from the repository root. Use three PowerShell
windows: **A** for the engine, **B** for commands/diagnostics, and **C** for
the static test page server.

## Build, offline tests, and device checks

In window B, run the plan's Windows build and offline suite first:

```powershell
cmake --build engine\build --config Release
.\engine\build\Release\engine_tests.exe --gtest_filter=-SignalingClient.*:-PublicSignalingBridge.*
```

The filtered executable command is the offline check. The CTest registration
is unfiltered and includes tests that connect to `ws://localhost:8443`; run it
only after a local signaling server is available:

```powershell
ctest --test-dir engine\build -C Release --output-on-failure
```

Find and validate the actual device before starting anything:

```powershell
$env:PYTHONPATH = "src"
$adb = (uv run python -c "from server.adb_manager import _find_adb; print(_find_adb() or '')").Trim()
if (-not $adb) { throw "adb was not found; put adb on PATH or install/configure it." }
& $adb devices -l

# Replace this with one serial whose state is `device` in the output above.
$serial = "emulator-5554"
& $adb -s $serial get-state
& $adb -s $serial shell getprop ro.product.model
& $adb -s $serial shell wm size
```

For a physical device, use its USB serial; for an emulator, use the displayed
`emulator-####` serial. Stop here for `unauthorized`, `offline`, or any serial
that is not exactly one intended device.

Set one initial local port and scrcpy connection ID. They must be free; choose
different values for the reconnect later.

```powershell
$scrcpyPort = 27183
$reconnectPort = 27184
$scid = 1
$tier = "720"
& $adb -s $serial forward --list
```

## Launch the engine and capture its ready record

In window A, repeat the selected launch values and clear optional engine
configuration so no stale environment enables WHEP capability auth or public
signaling. Environment variables and PowerShell variables are per-window:

```powershell
$serial = "emulator-5554"  # use the serial selected in window B
$scrcpyPort = 27183
$scid = 1
$tier = "720"
Remove-Item Env:ENGINE_WHEP_CAPABILITY_SECRET -ErrorAction SilentlyContinue
Remove-Item Env:ENGINE_LOCAL_ICE_SERVERS -ErrorAction SilentlyContinue
Remove-Item Env:ENGINE_SIGNALING_URL -ErrorAction SilentlyContinue
Remove-Item Env:ENGINE_SIGNALING_TOKEN -ErrorAction SilentlyContinue
Remove-Item Env:ENGINE_PUBLIC_ICE_SERVERS -ErrorAction SilentlyContinue
```

Preferred launch (window A): `test.ps1` builds unless `-SkipBuild` is supplied,
starts a **fresh** scrcpy server, then runs the engine. `Tee-Object` retains
the ready record and engine stdout for evidence. Do not add `2>&1`: Windows
PowerShell 5 converts the engine's intentional stderr diagnostics into
`NativeCommandError` records when the streams are merged.

```powershell
.\engine\test.ps1 -Serial $serial -Port $scrcpyPort -Scid $scid -Tier $tier |
  Tee-Object -FilePath .\engine\test\e2e-engine.stdout.log
```

Wait for one JSON line such as (ports and dimensions are OS/device assigned):

```json
{"instance_name":"poc-instance","pid":1234,"whep_port":8000,"admin_port":8001,"generation":0,"width":720,"height":1280}
```

The ready record must appear before opening the page. In window B, derive the
actual URLs from the captured line; do not substitute a guessed port:

```powershell
$readyLine = Get-Content .\engine\test\e2e-engine.stdout.log |
  Where-Object { $_ -match '^\{' } | Select-Object -Last 1
if (-not $readyLine) { throw "No ready JSON record in e2e-engine.stdout.log." }
$ready = $readyLine | ConvertFrom-Json
$whepUrl = "http://127.0.0.1:$($ready.whep_port)/whep"
$adminUrl = "http://127.0.0.1:$($ready.admin_port)"
$ready | ConvertTo-Json
"WHEP:  $whepUrl"
"Admin: $adminUrl"
```

`test.ps1` is only a launcher. The exact manual equivalent, useful when
isolating a launcher problem, is:

```powershell
$env:PYTHONPATH = "src"
uv run python -c "import sys; from server.scrcpy_session import _start_server, _find_adb; adb = _find_adb(); raise SystemExit(0 if adb and _start_server(adb, sys.argv[1], int(sys.argv[2]), scid=int(sys.argv[3]), tier=sys.argv[4]) else 1)" $serial $scrcpyPort $scid $tier
if ($LASTEXITCODE -ne 0) { throw "Fresh scrcpy-server start failed." }
& .\engine\build\Release\engine.exe poc-instance $scrcpyPort |
  Tee-Object -FilePath .\engine\test\e2e-engine.stdout.log
```

In either launch path, prove that the expected forward exists:

```powershell
& $adb -s $serial forward --list
```

## Admin health checkpoint

The admin listener is loopback-only, so run these commands on the Windows Host
PC. It should report `connected`, the ready-record generation, and non-zero
device dimensions:

```powershell
$health = Invoke-RestMethod -Method Get -Uri "$adminUrl/admin/health"
$health | ConvertTo-Json
if ($health.state -ne "connected") { throw "Source is not connected: $($health.state)" }
if ([uint64]$health.generation -ne [uint64]$ready.generation) { throw "Health generation differs from ready record." }
```

## Serve the test page and verify the first peer

The engine **does not serve** `engine/test/test_page.html`: `main.cpp` only
registers WHEP routes on its WHEP listener and admin routes on its separate
admin listener. Serve the checked-in page independently in window C:

```powershell
uv run python -m http.server 8088 --directory engine\test
```

In window B, open the static page with the derived WHEP endpoint. This is the
correct URL; `http://127.0.0.1:<whep_port>/test_page.html` is not an engine
route. WHEP supplies the CORS headers needed for this separate origin.

```powershell
$pageUrl = "http://127.0.0.1:8088/test_page.html?whep=$whepUrl"
Start-Process $pageUrl
```

In the first tab, wait for `receiving video` and confirm all of the following:

- The live device screen is visibly non-black. Make the device screen change;
  a status message alone is insufficient.
- In `chrome://webrtc-internals` (or Edge's `edge://webrtc-internals`), the
  matching inbound video `framesDecoded` value is above zero and climbs while
  the device changes.
- The page's input DataChannel is open (console shows `input channel open`).
- Click a distinctive device UI target in the video. The matching device action
  must be visible in the video or independently confirmed from device output.

Save a screenshot of the rendered page and the WebRTC stats before continuing.

If Chromium reports `The order of m-lines in answer doesn't match order in
offer`, this checkpoint fails before ICE or scrcpy video is involved. Confirm
the served page lists `addTransceiver` before `createDataChannel`, rebuild the
current `engine.exe`, and retain the logged offer/answer `m=` and `a=mid:` lines
if it still fails. Do not work around it by editing or reordering returned SDP.

## Second independent local peer

Without closing or reloading the first tab, open `$pageUrl` in a different
browser profile/incognito window (or a second browser). It creates a separate
WHEP `POST` and therefore a separate peer. Confirm both tabs render live,
their `framesDecoded` counters advance, and using the second peer does not
interrupt the first.

For a LAN second client, use the Windows Host PC's reachable LAN address in
`$whepUrl` instead of `127.0.0.1`, and serve the page from a reachable address
with an appropriate firewall rule. Keep the admin calls on the Host PC.

## Generation-based reconnect with the original tab left open

Keep the original tab open and do not reload it or create a new WHEP offer.
Record its current WebRTC stats. Then, in window B, get the authoritative
generation, relaunch the same scrcpy connection ID on a **different** local
port, and submit exactly generation + 1. The helper kills the prior server for
that `scid`, launches a fresh one, and adds the new forward.

```powershell
# Re-establish these values in window B; PowerShell variables are window-local.
[string]$serial = "emulator-5554"  # use the serial selected earlier
[int]$reconnectPort = 27184         # must differ from the initial port
[int]$scid = 1                      # same scrcpy connection ID as the initial run
[string]$tier = "720"
$env:PYTHONPATH = "src"
$adb = (uv run python -c "from server.adb_manager import _find_adb; print(_find_adb() or '')").Trim()
if (-not $adb) { throw "adb was not found." }
if (-not $adminUrl) { throw "adminUrl is missing; derive it from the ready record first." }

$healthBefore = Invoke-RestMethod -Method Get -Uri "$adminUrl/admin/health"
$nextGeneration = [uint64](([uint64]$healthBefore.generation) + 1)

uv run python -c "import sys; from server.scrcpy_session import _start_server, _find_adb; adb = _find_adb(); raise SystemExit(0 if adb and _start_server(adb, sys.argv[1], int(sys.argv[2]), scid=int(sys.argv[3]), tier=sys.argv[4]) else 1)" $serial $reconnectPort $scid $tier
if ($LASTEXITCODE -ne 0) { throw "Replacement scrcpy-server start failed." }
& $adb -s $serial forward --list

$reconnectBody = @{
  scrcpy_port = [int]$reconnectPort
  generation = [uint64]$nextGeneration
} |
  ConvertTo-Json -Compress
$reconnectBody
$reconnect = Invoke-RestMethod -Method Post -Uri "$adminUrl/admin/reconnect" `
  -ContentType "application/json" -Body $reconnectBody
$reconnect | ConvertTo-Json

$healthAfter = Invoke-RestMethod -Method Get -Uri "$adminUrl/admin/health"
$healthAfter | ConvertTo-Json
```

Pass only if the POST returns `accepted: true`, health returns the requested
new generation and `connected`, and the existing first tab resumes visible
video within a few seconds. Its peer connection must remain the same: no page
reload, no new WHEP `POST`/offer-answer exchange, and no ICE restart. Its
`framesDecoded` counter must resume climbing. The second peer should also
continue; note any deviation as a failure.

## Clean shutdown and evidence

Close both browser peers, then stop window A with `Ctrl+C` and require its
`Stopped.` line. Stop the static server in window C with `Ctrl+C`. Finally
remove both test forwards and stop the test scrcpy server:

```powershell
& $adb -s $serial forward --remove "tcp:$scrcpyPort"
& $adb -s $serial forward --remove "tcp:$reconnectPort"
$scidHex = $scid.ToString("x")
& $adb -s $serial shell "pkill -f 'scrcpy-server.*scid=$scidHex'"
```

Retain this evidence with the result:

- `e2e-engine.stdout.log`, including ready JSON and clean `Stopped.` output.
- The build/offline-test/CTest output and `adb devices -l` / forward listing.
- Initial and post-reconnect `/admin/health` JSON, plus the reconnect request
  body and response.
- First- and second-peer screenshots or screen recordings, including
  `framesDecoded` before/after reconnect and proof of the touch action.
- Browser console output and a `webrtc-internals` dump/screenshot.

## Stop and collect on failure

Do not retry by layering another restart or code change onto a failed run.
Stop the test, preserve the stdout log, capture the browser console and WebRTC
diagnostics, then collect:

```powershell
& $adb devices -l
& $adb -s $serial forward --list
& $adb -s $serial logcat -d -v threadtime > .\engine\test\e2e-adb-logcat.txt
Get-Content .\engine\test\e2e-engine.stdout.log -Tail 200
```

Record the exact failed checkpoint, ready record, health payloads, device
model/serial, browser/version, and whether the failure was first-frame,
DataChannel input, multi-peer, or reconnect. Only then diagnose from the
captured evidence.

## Copyable result

```text
Engine manual E2E result: PASS | FAIL
Date/time:
Host / Windows version:
Browser / version:
Device type, model, and serial:
Build command/result:
Offline engine_tests result:
CTest result:
Ready record:
Initial /admin/health:
First peer: non-black video + framesDecoded climbing: PASS | FAIL
Touch input observed on device: PASS | FAIL
Second independent peer preserves first peer: PASS | FAIL
Reconnect port / requested generation / response:
Post-reconnect /admin/health:
Original tab: no reload, no renegotiation, no ICE restart, frames resume: PASS | FAIL
Clean shutdown: PASS | FAIL
Evidence locations:
Failure checkpoint and notes (if any):
```
