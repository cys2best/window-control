# One-command Python orchestration verification

This run takes more than five minutes when expiry is not skipped. It
intentionally stops the selected emulator and scrcpy source during recovery;
use a disposable LDPlayer instance or an authorized test device.

Run this on the Windows Host PC from the repository root:

```powershell
.\engine\verify-python-orchestration.ps1
```

The command builds the Release engine, runs the focused Python and offline
engine tests, discovers one ready ADB device, starts the auth-free local
signaling relay and WindowControl app with a fresh WHEP secret, serves the
token-aware browser verifier, and opens it. It waits until the first WHEP token
is actually expired before selecting a second token, then runs quality,
scrcpy-death, engine-death, source-loss-plus-emulator-removal, and cleanup
checks with conditional polling. It pauses only for visible video/touch/
continuity confirmations. `-SkipBuild`, `-SkipTests`, `-SkipExpiry`, and
`-KeepOnFailure` are available for reruns; use `-Serial <adb-serial>` when more
than one device is connected. `-SkipExpiry`
marks the expiry checkpoint `SKIP` and the command exits nonzero because the
mandatory matrix is incomplete. `-KeepOnFailure` leaves the app, owned engine,
and selected ADB forward for diagnosis; helper logs/processes are otherwise
cleaned when safe.

After the token checkpoint, the runner requests a 1080 quality transition,
then asks for explicit `KILL` confirmation before terminating this instance's
scrcpy-server and engine process. It uses `adb -s <selected-serial> emu kill`
for an emulator (or `adb disconnect <selected-serial>` for a non-emulator); if
that safest automatic removal command fails, disconnect the selected emulator
manually when prompted. The runner never calls `adb kill-server` and never
touches other devices. Finally, choose WindowControl's tray Exit yourself;
the runner never stops the app process and only verifies that it has exited.

The runner calls `/engine-select` and passes its returned WHEP token to the
browser in a URL fragment (never a server-visible query). The same-origin page
sends that token as a Bearer credential, records WHEP Location and DELETEs the
resource on unload, and displays ICE/DataChannel/resource/generation,
`framesDecoded`, dimensions, and click input. It does not fake video or claim
Windows behavior from macOS tests.

Prerequisites are Visual Studio/CMake/vcpkg, `uv`, `adb`, one authorized
Android/LDPlayer device, and Chrome or Edge. If the build directory has not
been configured, follow [`../BUILD_WINDOWS.md`](../BUILD_WINDOWS.md) once.
The runner sets `ENGINE_EXE_PATH`, `ENGINE_WHEP_CAPABILITY_SECRET`, and the
local relay URL for this process; it does not alter persistent Windows
configuration.

On any failure, the script stops and retains logs under
`engine/test/verification-<timestamp>/`; inspect the named checkpoint, engine
stdout/stderr, app log, relay log, and ADB forward listings before changing
code. A result is **not verified** until all eight prompts are marked PASS on
the Windows Host PC and the final forward/process cleanup is visible in the
evidence directory.

Copy/send back the entire `engine/test/verification-<timestamp>/` directory
(zip it if convenient) and the final `result.json`. The result summary is:

```text
Verification result: PASS | FAIL | INCOMPLETE
Checkpoint statuses: see result.json
Selected serial/index/port/scid: see result.json
Failure gate and retained-on-failure state: see result.json
Evidence directory: engine/test/verification-<timestamp>/
```
