# Engine Core Rewrite Final Review Fix Report

## Status

Implementation is complete for C1-C3, I1-I3, and I5-I6. The three requested
cheap minors in touched files were folded in. The two explicitly skipped
minors were not changed.

This macOS session cannot compile the Windows/libdatachannel/Winsock/
websocketpp portions. The portable source-video fan-out/H.264 subset was
compiled with warnings promoted to errors and all 15 tests passed. Windows
build, focused tests, full tests, live-signaling tests, and manual e2e remain
deferred and are listed exactly below.

## Findings

### C1 - PeerSession callback install/dispatch race

- Added `Impl::callbackMutex`.
- `SetInputCallback` and `SetOnStateChange` assign under that mutex.
- The libdatachannel state and DataChannel handlers copy the applicable
  `std::function` under the mutex, release it, and invoke the copy afterward.
- Added a real negotiated-peer stress regression that replaces the input
  callback while DataChannel messages are dispatched, then verifies a final
  sentinel reaches the latest callback.

Lock reasoning: neither callback executes while `callbackMutex` is held.
`InputRouter` may take `fingerMutex_`, query source state, and perform control
socket I/O without creating a callback-mutex lock-order edge.

The review's approximate code description matched the current source.

### C2 - shared IDR rate-limit race

- Added a dedicated `idrMutex_` around the timestamp check-and-update, making
  the gate atomic as one operation.
- `RequestIdr()` runs after that mutex is released.
- Added a 32-thread barrier regression expecting exactly one reset-video byte.

The mutex implementation was chosen instead of atomic `time_point`/CAS because
it directly protects the compound decision and has a small, non-I/O critical
section. The review's current-code description matched.

### C3 - media-thread peer teardown

- Added thread-safe `PeerRegistry::MarkFailed(id)`, stored as a registry-entry
  flag. It never removes or closes a session.
- `ScrcpySource` now supplies `MarkFailed`, not `Remove`, to
  `SourceVideoFanout`.
- `SourceVideoFanout` collects failed IDs, finishes all peer send attempts,
  then invokes the marker callbacks. This preserves independent-peer
  continuation even if marker bookkeeping briefly contends on the registry.
- The existing once-per-second `ReapDeadAndStalePeers()` treats the marker as a
  dead condition, erases/retains victims under the registry lock, and calls
  `Close()` only after unlocking.
- Added a registry regression proving a marked peer remains findable until the
  reaper runs, plus a portable ordering regression proving the healthy send
  precedes failed-peer bookkeeping.

The review matched the actual adapter: it called `registry_.Remove(id)` via
`SourceVideoFanout::RemoveFailedPeer`. One deliberate behavior refinement is
that marking occurs after the whole snapshot's send attempts rather than
between failed and healthy sends. Failed peers still remain registered until
housekeeping, as ruled.

### I1 - validate public offer before eviction

- Added additive `PeerRegistry::Adopt(kind, id, session)`. `Create`'s signature
  and normal behavior remain unchanged.
- `Adopt` rejects null sessions and mismatched `session->Id()` values, applies
  capacity/conflict rules, and closes displaced sessions only after unlock.
- `PublicSignalingBridge` now constructs and attaches a standalone session,
  calls `AnswerOffer`, adopts only after success, sends the answer, and logs a
  `[public_signaling] public peer ready ...` success line.
- An `AnswerOffer` exception returns without touching the registry; the failed
  standalone session is destroyed normally.
- Added direct Adopt coverage and a practical live-signaling regression where
  malformed SDP follows a valid public session and must preserve `public-1`.

The review's proposed parameter list did not prescribe an Adopt return type;
the implementation returns `bool` so Local adoption can report capacity or
invalid-session rejection. The Public caller checks that result even though a
valid Public adoption has no capacity rejection.

### I2 - canonical per-peer finger state and scroll bounds

The live sources were read directly:

- Python uses `ny2 = clamp(ny + dy * 120 / h, 0, 1)` and falls back to `ny`
  when `h == 0`.
- The browser sends scroll `dy` as exactly `-1` or `1`.

Changes:

- `FingerState` now stores normalized `x/y` as well as down/pointer state.
- `drag_move` and `drag_end` send only for an active down state.
- Accepted moves update the stored position.
- `click` clears that peer's drag state.
- `scroll` removes active state, sends best-effort UP at the stored current drag
  position, then emits the scaled/clamped DOWN/MOVE/UP gesture.
- Disconnect cleanup also sends UP at the stored current position instead of
  `(0, 0)`.
- Every finger-map lookup/update/copy occurs under `fingerMutex_`; every
  `Control()`, `Status()`, and `SendTouch()` call occurs after unlock.
- Removed the unused `<iostream>` include.
- Extended only the test fake with a mutex-protected byte snapshot of data it
  already receives. Tests independently decode fixed scrcpy touch-frame
  offsets.
- Added real-negotiated-PeerSession coverage for the complete
  start/move/end sequence and ignored out-of-state moves/ends, active-drag
  scroll cancellation with observable bounded coordinates, and click clearing
  active drag state.

Exact deviation from the Python line: Python cancels an active drag at the new
scroll message's `(nx, ny)`. The binding controller ruling requested UP at the
active drag's current position, so this implementation uses the tracked last
accepted drag position. The scroll scaling and height-zero behavior otherwise
match Python exactly.

### I3 - signaling shutdown/callback lifetime

`SignalingClient::Disconnect()` was hardened rather than relying on only a
`main.cpp` call:

- Connect startup and shutdown state are synchronized.
- If Disconnect wins a Connect/Disconnect race, a later Connect is rejected;
  if Connect wins, Disconnect sees the I/O thread and quiesces it.
- Disconnect is idempotent while idle, connecting, connected, already
  stopping, or stopped.
- It atomically suppresses and clears the stored message callback, clears the
  pre-open send queue, closes an open connection, stops ASIO, and joins the I/O
  thread before returning for non-I/O callers.
- Concurrent external disconnect callers share one join via lifecycle state
  and a condition variable.
- The lifecycle mutex is released during join. This lets a callback on the I/O
  thread call Disconnect while another thread is already joining, avoiding a
  callback/lifecycle deadlock.
- An I/O-thread caller requests shutdown but never self-joins. The Impl is now
  shared with the I/O closure; websocket handlers hold weak references. If the
  owner is destroyed on that thread, Impl remains alive through `run()`, then
  safely detaches its already-returning current thread handle.
- Message callbacks are copied under a callback mutex and invoked after
  unlock. A normal external Disconnect joins any callback already in flight,
  so no callback can still execute after Disconnect returns.
- `Send` drops after shutdown instead of accumulating unsendable messages.

`main.cpp` now explicitly disconnects signaling after stopping the HTTP
listeners. Its pointer declarations are ordered so signaling is destroyed
before `PublicSignalingBridge` on exceptional exits as well, closing the gap
that a normal-path call alone would leave. `std::stoi(argv[2])` was moved into
the top-level try.

Added live-server coverage that deliberately blocks an in-flight callback and
proves Disconnect waits, then proves a post-disconnect message cannot invoke a
callback. Added idle/pre-Connect idempotence and immediate-handshake shutdown
coverage.

The review correctly identified the missing main call. The binding ruling also
correctly noted that the actual Disconnect returned immediately while
connecting and that its eventual Impl destructor join was too late to protect
the bridge callback target; both paths were addressed.

### I5 - structured admin reconnect failure

- Wrapped `.get<int>()`/`.get<uint64_t>()` in `json::exception` handling,
  returning 400 for wrong field types and logging `[admin]` diagnostics.
- Wrapped `ScrcpySource::Reconnect` in `std::exception` handling, returning 502
  JSON with `accepted: false`, `error`, and `current_generation`, plus an
  `[admin] reconnect failed` log.
- Added a wrong-type HTTP regression and a deterministic fake handshake-close
  regression that expects structured 502 and generation 0.

The review's description matched the current handler and reconnect throws.

### I6 - duplicate registry ID overwrite under lock

- Extracted shared `CanInsertLocked` and `EvictConflictsLocked` helpers used by
  Create and Adopt.
- Capacity counting excludes an existing same-ID Local entry, so replacement
  keeps local count constant while a new unique Local still obeys the cap.
- `EvictConflictsLocked` retains and erases a duplicate ID, then applies
  Public-kind eviction. Create invokes it before constructing the replacement,
  preserving its pre-existing ordering; Adopt invokes it after receiving the
  prebuilt session. Both emplace under lock and close retained victims only
  after unlock.
- Adopt avoids closing the adopted session itself if the same pointer was
  already registered defensively.
- Added a capacity-one duplicate-Local regression verifying successful
  replacement and count 1.

The review's `operator[]` overwrite description matched the current source.

## Minors

Folded in the three requested touched-file minors:

- Removed unused `<iostream>` from `input_router.cpp`.
- Moved `std::stoi(argv[2])` inside main's top-level try.
- Added a public-signaling success log.

Skipped exactly as directed:

- No whitespace trimming change in `engine_config.cpp`.
- No WHEP failure-log change in `whep_handler.cpp`.

## Tests and evidence

All new behavior tests were written before their corresponding production
change. Existing test assertions were not edited.

### Honest portable RED

After adding `DeliversToHealthyPeersBeforeMarkingFailures` and before changing
fan-out production code:

```sh
clang++ -std=c++20 -Wall -Wextra -Werror \
  -Iengine/src -I/opt/homebrew/opt/googletest/include \
  engine/src/h264_nalu.cpp engine/src/source_video_fanout.cpp \
  engine/test/test_source_video_fanout.cpp \
  -L/opt/homebrew/opt/googletest/lib -lgtest -lgtest_main -pthread \
  -o /private/tmp/window-control-final-fanout-red && \
/private/tmp/window-control-final-fanout-red \
  --gtest_filter='SourceVideoFanout.DeliversToHealthyPeersBeforeMarkingFailures'
```

Observed failure: actual events were `{"mark:broken", "healthy"}` while the
required order was `{"healthy", "mark:broken"}`. Zero tests passed, one failed.

### Portable GREEN and final regression

Focused fan-out GREEN: 5/5 passed.

Fresh final command:

```sh
clang++ -std=c++20 -Wall -Wextra -Werror \
  -Iengine/src -I/opt/homebrew/opt/googletest/include \
  engine/src/h264_nalu.cpp engine/src/source_video_fanout.cpp \
  engine/test/test_h264_nalu.cpp engine/test/test_source_video_fanout.cpp \
  -L/opt/homebrew/opt/googletest/lib -lgtest -lgtest_main -pthread \
  -o /private/tmp/window-control-final-portable-tests && \
/private/tmp/window-control-final-portable-tests
```

Observed exit 0: 15 tests from 3 suites, all passed. `-Wall -Wextra -Werror`
reported no compile warnings. The linker emitted the known Homebrew warning
that its GTest archives target a newer macOS deployment version.

`git diff --check ddb65b5..HEAD` also exited 0. The branch diff contains only
the 21 scoped engine source/test files plus this report; preserved `.DS_Store`
files, `engine.txt`, and controller-owned `HANDOFF.md` remain unstaged.

### Deferred Windows verification

No Windows/libdatachannel/Winsock/websocketpp compilation or test execution is
claimed. Run from the repository root in a VS2022 developer shell:

```powershell
cmake -S engine -B engine\build `
  -DCMAKE_TOOLCHAIN_FILE=<path-to-vcpkg>\scripts\buildsystems\vcpkg.cmake `
  -DVCPKG_TARGET_TRIPLET=x64-windows
cmake --build engine\build --config Release
.\engine\build\Release\engine_tests.exe --gtest_filter="PeerSession.*:PeerRegistry.*:SourceVideoFanout.*:InputRouter.*:AdminHandler.*"
.\engine\build\Release\engine_tests.exe --gtest_filter=-SignalingClient.*:PublicSignalingBridge.*
```

For websocketpp/live-signaling tests, start the auth-disabled relay in another
PowerShell terminal:

```powershell
Set-Location infra\vps\signaling
Remove-Item Env:JWT_SECRET -ErrorAction SilentlyContinue
npm install
npm start
```

Then run:

```powershell
.\engine\build\Release\engine_tests.exe --gtest_filter="SignalingClient.*:PublicSignalingBridge.*"
.\engine\build\Release\engine_tests.exe
```

The plan's manual e2e gate is also still required. After starting scrcpy-server
for a real device as documented in `engine/test/README_e2e.md`:

```powershell
$env:ENGINE_WHEP_CAPABILITY_SECRET = ""
.\engine\build\Release\engine.exe my-instance 27183
```

Open the ready record's WHEP URL in `test_page.html`, verify video and input,
then stop with Ctrl+C and confirm browser peer shutdown.

## Commits

- `ac2c2d8efffccdbf1456a2d46293b889b8d216f6` - defer failed peer teardown to housekeeping
- `e0d84c6a4e282d823ddc2121aeb5159112283eb4` - synchronize peer callbacks and IDR gating
- `86ebb8587db1dd9eec8cc0c25be8d9947405c60f` - adopt validated public peers safely
- `e0d224041497f724a9c06b2da702cb6ec725cf4c` - port canonical per-peer finger state
- `b558cbc090543344cb01bb5270ae2722cb9c5c40` - quiesce signaling before shutdown
- `f360cc385d0d7a54f32151cacb0e905cd8322e1e` - return structured reconnect failures
- `f1069d68bfb386f1f06b3c06f753ae005b16ba63` - preserve registry create ordering
- `e4d4d27ee4e0dcffa31b40d442466c9377a5202f` - harden async regression lifetimes

## Concerns

- The new signaling lifecycle code is the highest-risk uncompiled portion:
  validate websocketpp `close`/`stop` behavior and MSVC types with the focused
  live-server regression before shipping.
- All new PeerSession/PeerRegistry/InputRouter/AdminHandler integration tests
  require the Windows dependency stack and have not executed here.
- The full engine rewrite still needs the plan-wide Windows build/full suite
  and real-device manual e2e gate; portable GREEN is not a substitute.

## Residual Fix Wave (human-authorized)

This wave was explicitly authorized after the normal final-review cap. It is
based on `440d705474755a60e4118dc62827f9e6471dbb8a` and changes only the two
confirmed residual Important findings.

### Stable identity for deferred send failures

The failed-target path no longer reduces a source snapshot to a peer ID:

- `SourceVideoPeerTarget` now owns a target-specific `markFailed` callback.
  The portable fan-out continues every send first, retains pointers into its
  immutable target snapshot for failed sends, and invokes those markers only
  after all sends have completed.
- `ScrcpySource` captures the snapshot's typed `shared_ptr<PeerSession>` in
  that target callback and calls `PeerRegistry::MarkFailed(peer->Id(), peer)`.
- `PeerRegistry::MarkFailed` compares the expected `shared_ptr` with the
  current entry while holding the registry mutex. A removed or same-ID
  replacement returns `false` and is not marked. A matching current entry has
  only its `failed` flag set; `Close()` remains exclusively in the periodic
  housekeeping reaper and remains outside the registry lock.

`FailureMarkerRetainsCapturedTargetIdentity` covers the portable boundary by
simulating a replacement during the failing send and proving that the deferred
marker retains generation 1 rather than looking up generation 2.
`OldSameIdSessionCannotMarkReplacementFailed` covers the registry boundary:
an old same-ID `shared_ptr` cannot mark the replacement, and a subsequent reap
leaves the replacement installed. Existing assertions were not changed.

Files:

- `engine/src/source_video_fanout.h`
- `engine/src/source_video_fanout.cpp`
- `engine/src/scrcpy_source.cpp`
- `engine/src/peer_registry.h`
- `engine/src/peer_registry.cpp`
- `engine/test/test_source_video_fanout.cpp`
- `engine/test/test_peer_registry.cpp`

### Deterministic malformed-offer regression lifetime

`MalformedOfferPreservesExistingPublicPeer` no longer treats a 250 ms delay as
proof that the malformed offer ran. A thread-safe test-only `std::cerr` buffer
waits for the existing production `AnswerOffer failed, dropping` diagnostic.
The test fails on timeout if the malformed message was dropped or never
processed. It then explicitly calls `Disconnect()` on both viewer and engine;
the engine disconnect joins/quiesces the callback thread before registry and
answer-count assertions run.

An RAII disconnect guard covers every earlier `ASSERT_*` return. Declaration
order keeps the diagnostic buffer, both clients, the bridge, router, registry,
source, and captured atomic alive until that guard has quiesced both signaling
clients. The diagnostic capture itself outlives the source/bridge teardown, so
no asynchronous logger can retain its temporary stream buffer. No production
test hook was added. The other async regressions were not changed.

File: `engine/test/test_public_signaling.cpp`.

### TDD evidence and verification

The two new regressions were added before production changes. The portable RED
command was:

```sh
clang++ -std=c++20 -Wall -Wextra -Werror \
  -Iengine/src -I/opt/homebrew/opt/googletest/include \
  engine/src/h264_nalu.cpp engine/src/source_video_fanout.cpp \
  engine/test/test_source_video_fanout.cpp \
  -L/opt/homebrew/opt/googletest/lib -lgtest -lgtest_main -pthread \
  -o /private/tmp/window-control-residual-fanout-red
```

Observed exit 1: the desired three-field target had no matching initializer,
and `SourceVideoFanout` had no two-argument constructor. This directly exposed
the old global ID-only marker API.

The required prior 15-test portable subset plus the new fan-out regression was
then rebuilt and run fresh:

```sh
clang++ -std=c++20 -Wall -Wextra -Werror \
  -Iengine/src -I/opt/homebrew/opt/googletest/include \
  engine/src/h264_nalu.cpp engine/src/source_video_fanout.cpp \
  engine/test/test_h264_nalu.cpp engine/test/test_source_video_fanout.cpp \
  -L/opt/homebrew/opt/googletest/lib -lgtest -lgtest_main -pthread \
  -o /private/tmp/window-control-residual-portable-tests && \
/private/tmp/window-control-residual-portable-tests
```

Observed exit 0: 16 tests from 3 suites passed. `-Wall -Wextra -Werror`
reported no compiler warnings. The linker emitted only the already-known
Homebrew GTest/newer-macOS deployment warning. `git diff --check` also exited
0.

No Windows/libdatachannel/websocketpp compilation or live-signaling execution
is claimed. From a VS2022 developer shell, run:

```powershell
cmake -S engine -B engine\build `
  -DCMAKE_TOOLCHAIN_FILE=<path-to-vcpkg>\scripts\buildsystems\vcpkg.cmake `
  -DVCPKG_TARGET_TRIPLET=x64-windows
cmake --build engine\build --config Release
.\engine\build\Release\engine_tests.exe --gtest_filter="SourceVideoFanout.*:PeerRegistry.*"
```

With the auth-disabled relay running as documented in the earlier deferred
verification section, run the live regression and then the full suite:

```powershell
.\engine\build\Release\engine_tests.exe --gtest_filter="PublicSignalingBridge.MalformedOfferPreservesExistingPublicPeer"
.\engine\build\Release\engine_tests.exe
```

### Commit and concerns

- `de833209e69d1d1ac9bbb5d943f408e8f4bec038` - retain stable peer identity
  through deferred failure marking and make malformed-offer completion and
  teardown deterministic.

The registry and live-signaling regressions remain Windows-deferred because
this macOS environment cannot build the libdatachannel/Winsock/websocketpp
engine target. The existing plan-wide Windows build, full suite, and real-device
manual e2e gate therefore remain required. No other residual concern was found
in the scoped source/interface/lock/lifetime trace.
