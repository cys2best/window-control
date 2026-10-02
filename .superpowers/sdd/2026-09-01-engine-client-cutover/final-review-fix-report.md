# Final review fix report

Date: 2026-09-02

## Outcome

Implemented all eight Critical/Important final-review fixes while preserving the engine-only selection path, fresh capability use, token redaction rules, no-fallback behavior, and the exact production `/instances/{id}/select` contract. Browser asset changes bump `VERSION` from `2.3.19` to `2.3.20`.

This macOS run does not claim C++/Windows, packaged installer, device, public VPS, or soak behavior. One signaling test rerun also remains unverified because the sandbox rejects local port binding and the requested sandbox-external rerun was rejected by the account escalation usage limit.

## Findings and TDD evidence

### 1. Production browser input sender was treated as inactive

- RED: changed `tests/client/browser_cutover.test.js` to construct the harness input with the production `WindowControlInput.createSender()` API, whose returned sender intentionally has no public `channel`. The focused browser run failed three input assertions: key/IDR/echo, drag/scroll, and tap messages were all absent.
- GREEN: `_activeInput()` now returns the adopted sender directly. Readiness and closed-channel suppression remain encapsulated by the production sender. The real-sender harness proves key, IDR, echo, drag, and proportional scroll delivery without a fake-only `channel` property.
- Final evidence: `node --test tests/client/*.test.js` — 45 passed, 0 failed.

### 2. PointerEvent-capable touch devices missed two-finger scrolling

- RED: added an `_startApp()` regression with `PointerEvent` and `navigator.maxTouchPoints = 5`; dispatching two touch points produced no scroll message.
- GREEN: `_startApp()` now installs touch plus mouse handling on touch-capable devices, and selects pointer handling only for non-touch PointerEvent devices. The regression observes the normalized two-finger scroll message through the production sender.
- Final evidence: included in the 45/45 browser run.

### 3. Final verifier rejected production `w`/`h` selection metadata

- RED: added a real-adapter response test that exercises the exact production payload and then adds an extra legacy `width` field. Before the fix, the non-exact response was accepted (`DID NOT RAISE`).
- GREEN: added the exact 12-field production field set (`ok`, `id`, `serial`, `name`, `w`, `h`, `whep_url`, `whep_token`, `signaling_url`, `signaling_token`, `ice_servers`, `generation`). `RealCutoverDeps.select()` validates that raw payload before adding verifier-only `request_path`; the downstream verifier validates the exact enriched shape, identity, positive integer `w`/`h`, ICE metadata, endpoints, and token secrecy. Fakes now use `w`/`h` and the production shape.
- Final evidence: `uv run pytest tests/test_engine_cutover_verifier.py ... tests/test_config.py -v` — 211 passed, 133 existing deprecation warnings.

### 4. FAIL/INCOMPLETE retained helpers without `-KeepOnFailure`

- RED: parametrized FAIL and INCOMPLETE tests expected five registered engines and the app to be stopped in reverse ownership order; neither outcome stopped anything. A real-adapter exact-identity test also failed because no cleanup method existed.
- GREEN: the verifier now cleans owned helpers on FAIL/INCOMPLETE by default and retains them only when `keep_on_failure` is true. `RealCutoverDeps.cleanup_owned_helpers()` considers only registered `OwnedProcess(pid, started_at)` values, skips PID-reused identities, deduplicates, terminates with bounded waits, and records bounded cleanup failure details. Browser and prompt cleanup remains unconditional.
- Final evidence: both fake state-machine outcomes and the real exact-PID/start-time regression pass in the 211-test matrix.

### 5. Mobile posted partially gathered non-trickle offers

- RED: added a fake-timer regression that emits an srflx candidate and advances the old 300 ms cap while ICE remains `gathering`; the WHEP POST occurred early.
- GREEN: `waitForIceGatheringComplete()` now resolves only at `iceGatheringState === "complete"` and rejects `ice-gathering-timeout` at the remaining session deadline. `connectWhep()` cannot POST a partial SDP after an srflx candidate or arbitrary short delay.
- Final evidence: mobile Jest — 14 suites passed, 62 tests passed; `npx tsc --noEmit` — exit 0.

### 6. Mobile accepted successful WHEP responses without `Location`

- RED: added a 201 response with no `Location`; the connection remained pending until the general timeout instead of failing with `missing-location`.
- GREEN: a successful response without `Location` now rejects with the typed `missing-location` error and closes the peer. Every adopted WHEP session therefore has a deletable resource URL.
- Final evidence: included in the 62/62 mobile Jest run.

### 7. Engine peer callbacks could outlive `InputRouter`

- RED authored before implementation:
  - `PeerRegistry.CloseAllDrainsEveryOwnedPeer`
  - `PeerSession.ClearCallbacksSuppressesLaterInputDispatch`
  - `InputRouter.ShutdownPeersClearsCallbacksAndDrainsRegistry`
- RED execution was not possible on this Darwin host: `cmake --build engine/build --config Release` exited 127 with `zsh: command not found: cmake`. No C++ GREEN claim is made.
- Implementation: `PeerSession::ClearCallbacks()` waits behind its callback mutex and clears input/state callbacks; `InputRouter::ShutdownPeers()` clears every snapshot callback while the router is live, releases held fingers, then drains the registry through `PeerRegistry::CloseAll()`. `InputPeerShutdownGuard` is declared after `InputRouter`, so normal and exceptional cleanup occurs before router/source/registry destruction. Servers and signaling are stopped/disconnected before the explicit normal-path drain.
- Required remaining GREEN: build and run the unfiltered C++ suite on Windows, including these three regressions, behind the Node relay.

### 8. Persisted pinned quality was not applied to replacement engines

- RED: added a saved-`1080` replacement/race regression; no quality POSTs occurred. Added a saved-`720` regression because the replacement runtime starts with an unknown tier; it also observed no quality request.
- GREEN: an adopted ready replacement applies the saved pin through `/instances/{serial}/quality`. Generation, active serial, and current preference guards prevent a stale completion from mutating UI state; stale selections never issue the quality request. The manual hold interval remains 60 seconds.
- Final evidence: included in the 45/45 browser run.

## Changed files

- Browser: `src/client/app.js`, `src/config.py`, `tests/client/browser_cutover.test.js`
- Verifier: `scripts/verify_engine_cutover.py`, `tests/test_engine_cutover_verifier.py`
- Mobile: `mobile/src/webrtc/whep.ts`, `mobile/src/webrtc/whep.test.ts`
- Engine shutdown: `engine/src/main.cpp`, `engine/src/input_router.h`, `engine/src/input_router.cpp`, `engine/src/peer_registry.h`, `engine/src/peer_registry.cpp`, `engine/src/peer_session.h`, `engine/src/peer_session.cpp`
- Engine regressions: `engine/test/test_input_router.cpp`, `engine/test/test_peer_registry.cpp`, `engine/test/test_peer_session.cpp`
- Managed report: `.superpowers/sdd/2026-09-01-engine-client-cutover/final-review-fix-report.md`

Unrelated user-owned `.DS_Store` files, `engine.txt`, and `HANDOFF.md` were not changed or staged by this fix wave.

## Verification results

- `uv run pytest tests/test_engine_cutover_verifier.py tests/test_engine_admin.py tests/test_engine_runtime.py tests/test_engine_orchestrator.py tests/test_instance_manager.py tests/test_app.py tests/test_app_auth.py tests/test_main.py tests/test_build_files.py tests/test_config.py -v` — 211 passed, 133 warnings, exit 0.
- `node --test tests/client/*.test.js` — 45 passed, 0 failed, exit 0.
- `cd mobile && npm test -- --runInBand` — 14 suites passed, 62 tests passed, exit 0.
- `cd mobile && npx tsc --noEmit` — exit 0.
- `.venv/bin/python -m py_compile scripts/verify_engine_cutover.py src/config.py` — exit 0.
- `git diff --check` — exit 0.
- `uv run pytest tests/ -q` — baseline collection stopped with exactly the two documented unrelated errors: `tests/test_auto_unlock.py` cannot import `CREDENTIAL_SERVICE`, and `tests/test_window_manager.py` cannot import removed `server.window_manager`; exit 2, no new collection error.
- `cd infra/vps/signaling && npm test` inside the sandbox — 11 failures, all from `listen EPERM: operation not permitted 0.0.0.0`. The required sandbox-external rerun was attempted and rejected before process creation because the escalation account usage limit was reached. This is an unresolved verification gate, not a product-test failure claim.
- `cmake --build engine/build --config Release` — not run successfully; exit 127 because `cmake` is unavailable on this Darwin host. Per plan, C++ and packaged behavior must be verified on Windows.

## Remaining Windows and external gates

1. Run `infra/vps/signaling` Jest where local listen sockets are permitted.
2. On Windows, build the engine and run the complete unfiltered C++ suite behind the Node relay, including the new shutdown regressions.
3. Run Windows CI/package smoke and verify the installed engine executable/DLLs and exact installer-owned firewall rule/path.
4. Execute `engine/verify-engine-cutover.ps1` with exactly five ready devices and approved performance evidence.
5. Complete every real local browser, public browser/VPS, bearer-auth mobile, local/public race, 20-switch cleanup, tier transition, scrcpy recovery, engine recovery/fresh-selection, eight-hour soak, installer/uninstaller, and tray-exit checkpoint. Preserve PASS evidence; any skip or shortened soak remains INCOMPLETE.

## Self-review

- Re-read the complete intended diff after implementation and corrected prompt/browser cleanup placement before the final focused runs.
- Confirmed browser tests use the production sender surface; the only remaining `session.input.channel` reference is an engine-session ownership assertion, not the browser application harness.
- Confirmed selection validation occurs on the raw 12-field response before verifier metadata is added and that no endpoint includes capability tokens.
- Confirmed default failure cleanup is limited to registered exact PID/start-time identities and `-KeepOnFailure` is the sole retention path.
- Confirmed the WHEP POST is impossible before complete ICE gathering and a successful POST cannot be adopted without a deletable `Location`.
- Confirmed C++ declaration/destruction order keeps `InputRouter` alive while peer callbacks are cleared and peers drained; Windows compilation/execution remains mandatory.
- Confirmed stale quality applications are generation/serial/preference guarded and that replacement `720` is explicitly posted despite the UI default.
- `git diff --check` is clean. No unrelated user files are included in the intended commit.

## Concerns

- Completion remains blocked on the signaling rerun environment and the plan-mandated Windows/C++/device/package/soak gates. The implementation is ready for those gates, but this report intentionally does not elevate the plan to complete.
