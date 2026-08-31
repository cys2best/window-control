# Windows Python orchestration verification

This is a Windows Host PC acceptance run against a real `engine.exe`, browser,
and disposable LDPlayer instance. It takes more than five minutes because the
runner waits for a real WHEP capability to expire. It deliberately stops the
selected scrcpy source, engine, and emulator during recovery checks.

## Prerequisites and preflight

Install Visual Studio/CMake/vcpkg, `uv`, `adb`, and Chrome or Edge. Configure
the engine build once with [`../BUILD_WINDOWS.md`](../BUILD_WINDOWS.md), if
needed. Exit any retained WindowControl app from its tray and close any
pre-existing `engine.exe` before starting.

Exactly one ADB device may be in state `device`, including when `-Serial` is
used. Check in PowerShell:

```powershell
adb devices
```

The output must contain exactly one device row ending in `device`. Manually
close every other LDPlayer instance before continuing. Do not run automated
commands that kill, disconnect, or alter unknown emulators; the verifier is
allowed to remove only the single serial named on its command line.

Use an authorized disposable instance because matrix checkpoint 7 removes it.
The examples below assume its serial is `emulator-5554`.

## Two-terminal file-confirmation workflow (no stdin)

Open two PowerShell terminals at the repository root.

In terminal 1, start the complete verifier:

```powershell
.\engine\verify-python-orchestration.ps1 -Serial emulator-5554 -FilePrompts
```

`-FilePrompts` never reads stdin. When a visual/manual decision is required,
terminal 1 prints the active checkpoint and instruction, then waits.

On the current Host PC, terminal stdin has appeared unreliable while the
verifier-owned PyQt GUI is running or foreground focus changes; closing the GUI
has released input in prior runs. Code inspection found no keyboard hook, and
this observation does not establish a PowerShell bug. File-prompt mode is the
supported workaround because confirmations arrive through files instead of
terminal input.

Inspect the browser and perform the requested action. In terminal 2 submit the
result:

```powershell
.\engine\verify-python-orchestration.ps1 -Confirm PASS
```

Use this instead when the checkpoint failed:

```powershell
.\engine\verify-python-orchestration.ps1 -Confirm FAIL
```

`PASS` preserves the normal prompt semantics and advances the run. `FAIL`
returns a non-PASS answer to the verifier, fails that gate, and starts normal
failure cleanup/evidence capture. Stop on the first failure; do not submit
additional confirmations or reinterpret a failed checkpoint as verified.

Each wait publishes `active-prompt.json` inside that run's ignored
`engine/test/verification-<timestamp>/` evidence directory. It contains only
the live verifier PID, a new random nonce, checkpoint, instruction, and allowed
results—never WHEP/auth credentials. `-Confirm` considers only prompt files
whose verifier PID is live. It refuses when no live prompt exists or when
multiple verifier runs are active, then atomically writes a nonce-matched
response beside the selected prompt. The verifier consumes that response once,
deletes both active files, and gives the next checkpoint a different nonce, so
an old `PASS` cannot satisfy a later wait.

## What terminal 1 verifies

The runner builds the Release engine and runs the focused Python and offline
engine suites. It then creates one sanitized child environment with a fresh
WHEP secret, blank auth/tunnel settings, and a loopback signaling URL; starts
the local relay and WindowControl app; serves the browser verifier; and checks
all eight Task 9 matrix items:

1. Discovery starts exactly one owned `engine.exe` before selection.
2. `/engine-select` returns a non-loopback WHEP URL and the browser negotiates
   authenticated WHEP video, rising `framesDecoded`, DataChannel, and click
   input.
3. After the original WHEP capability expires, a fresh selection/token opens
   an independent live verifier page.
4. `/quality` advances generation/dimensions while preserving the owned engine
   PID, WHEP endpoint, and existing peer continuity.
5. Killing the selected scrcpy-server triggers watchdog recovery while
   preserving the engine PID, WHEP endpoint, and exact selected ADB forward.
6. Killing the owned engine triggers one replacement process and a new dynamic
   WHEP endpoint while retaining the selected forward.
7. A second selected-source loss is triggered immediately before removal of
   that selected emulator; the API instance, owned engine, and its forward must
   disappear.
8. After you choose WindowControl's tray **Exit**, no app, engine, or selected
   forward may remain.

The browser selection is encoded in a URL fragment, never a server-visible
query. The page sends the WHEP token only as a Bearer credential, records the
WHEP resource `Location`, deletes that resource on unload, and displays ICE,
DataChannel, generation, dimensions, resource state, and `framesDecoded`.

For checkpoint 3, terminal 1 and `verification.log` first report the remaining
expiry duration, then bounded periodic remaining-time updates, followed by a
completion line. These messages contain no token. Leave terminal 1 running and
wait for the next file-confirmation instruction; do not create a replacement
token manually.

For checkpoint 7 the runner uses `adb -s <selected-serial> emu kill` for the
selected emulator, or `adb disconnect <selected-serial>` for a selected
non-emulator. If that scoped command fails, disconnect only the selected device
manually and confirm the corresponding prompt. It never calls
`adb kill-server` or targets another device. For checkpoint 8, use the tray
Exit yourself; the runner never force-kills WindowControl.

## Default stdin mode and rerun flags

Interactive terminals may omit `-FilePrompts`; the original stdin prompts are
unchanged:

```powershell
.\engine\verify-python-orchestration.ps1 -Serial emulator-5554
```

`-SkipBuild`, `-SkipTests`, `-SkipExpiry`, and `-KeepOnFailure` remain
available. `-SkipExpiry` records `SKIP` and exits nonzero because the mandatory
matrix is incomplete. `-KeepOnFailure` retains the verifier-owned app, engine,
and selected forward for diagnosis; helper relay/page processes and active
prompt/response files are still cleaned when safe.

## Results and evidence

On any failure, stop and inspect the named gate, `result.json`, `commands.log`,
`verification.log`, app/relay/page logs, engine output, and ADB forward state in
`engine/test/verification-<timestamp>/`. A result is **not verified** until all
automatic invariants and manual confirmations pass on the Windows Host PC.

Copy/send back the entire evidence directory (zip it if convenient), including
`result.json`:

```text
Verification result: PASS | FAIL | INCOMPLETE
Checkpoint statuses: see result.json
Selected serial/index/port/scid: see result.json
Failure gate and retained-on-failure state: see result.json
Evidence directory: engine/test/verification-<timestamp>/
```
