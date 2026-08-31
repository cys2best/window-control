# One-command Python orchestration verification

Run this on the Windows Host PC from the repository root:

```powershell
.\engine\verify-python-orchestration.ps1
```

The command builds the Release engine, runs the focused Python and offline
engine tests, discovers one ready ADB device, starts the auth-free local
signaling relay and WindowControl app with a fresh WHEP secret, serves the
token-aware browser verifier, and opens it. It pauses for PASS/FAIL
confirmation at each of the eight real-engine checkpoints. `-SkipBuild`,
`-SkipTests`, `-NoBrowser`, and `-KeepLogs` are available for reruns; use
`-Serial <adb-serial>` when more than one device is connected.

The browser page calls `/engine-select`, sends its returned WHEP token as a
Bearer credential, reports generation and `framesDecoded`, supports clicks,
and exposes quality/fresh-selection controls. It does not fake video or claim
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
