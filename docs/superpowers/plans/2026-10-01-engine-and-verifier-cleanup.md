# Engine Signaling, Verifier and Relay Removal — Implementation Plan (Plan B)

> **Public-access removal direction superseded (2026-10-02):** See [Direct WebRTC With Shared TURN Fallback](../specs/2026-10-02-direct-webrtc-shared-turn-design.md). Keep this completed cleanup as history. The replacement uses the Python host for public signaling; it does not require restoring the removed engine WSS client or old verifiers unchanged.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Nothing left in the repo talks to, tests, installs or documents a public signaling relay: the C++ engine's WSS signaling client, the cutover verifier scripts and wrappers, the CI relay step, `infra/vps/signaling` and the docs that describe them are gone.

**Architecture:** Delete in dependency order so every commit still passes its own tests: engine sources first (nothing else imports them), then the CI step that ran the relay-backed tests, then the verifier scripts that started the relay, then the relay itself, then docs. Every Python-visible effect is pinned by a test that runs on macOS; the C++ change can only be compiled by the `build-engine` CI job.

**Tech Stack:** C++20 / CMake / vcpkg / GoogleTest (engine, Windows only); Python 3.11 / pytest via `uv run`; npm workspaces; GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-01-local-only-pairing-design.md` ("Engine, CI, verifiers (Plan B)" and "Infrastructure"). Plan A (`docs/superpowers/plans/2026-10-01-local-only-pairing.md`) is already merged on this branch; it left these four pieces for this plan.

## Global Constraints

- Python commands run through `uv run` (`uv run pytest`, `uv run python`), never bare `python` / `pytest`.
- Commit format: `<type>(optional-scope): imperative description`. No plan names, task numbers, agent names, `Co-Authored-By`, or AI attribution in commit messages.
- New files under `docs/` need `git add -f` (`docs/*` is gitignored).
- The working tree carries the user's unrelated uncommitted and untracked files (`.gitignore`, `AGENTS.md`, `CLAUDE.md`, `.claude/`, `mobile/`, `infra/.DS_Store`, `infra/terraform/*` leftovers, …). Stage by explicit path only. Never `git add -A` / `git add .` at the repo root, never `git stash`, `git reset --hard`, `git checkout -- .`, `git clean`, or `rm -rf`. Delete tracked files with `git rm`. Untracked leftovers inside a deleted directory (for example `node_modules/`) stay on disk; report them, do not remove them.
- The local WHEP path must keep working unchanged: the engine still receives only `ENGINE_WHEP_CAPABILITY_SECRET` and `ENGINE_LOCAL_ICE_SERVERS`; `OpenSSL`, `nlohmann_json`, `httplib`, `libdatachannel`, `ws2_32` stay linked.
- `PeerKind::Public`, `PeerRegistry::Adopt`, `HasPublicPeer` and the `public_peer` field of `/admin/health` stay. `src/server/engine_admin.py` validates that field as a bool and its fixtures/tests depend on it; removing it is a separate cross-language change (see "Not in this plan").
- `tests/test_config.py` and `tests/test_engine_runtime.py` already hold "must be absent" regression guards for `VPS_SIGNALING_URL`, `ENGINE_SIGNALING`, `ENGINE_SESSION`, `public_session`, `signaling_url`. Do not weaken them.
- `docs/superpowers/`, `CHANGELOG.md` history and `.agent-sync/` archives are history; do not rewrite them.
- The baseline before Task 1: `uv run pytest tests/ apps/desktop/ -q` passes (642 passed, 1 skipped), `npm run test:core`, `npm run test:ui`, `npm test -w apps/web` pass, `npm run test:signaling` passes (18).
- C++ cannot be compiled on macOS (`engine/CMakeLists.txt` hard-fails off Windows). A task that touches C++ states in its report that compilation is NOT verified locally; the `build-engine` workflow on the pushed branch is the only validation.

## Review Focus

1. **A C++ include or symbol still points at a deleted header.** Expected: `engine_core` and `engine_tests` still configure and build. Pinned in Task 1 by a Python test that greps every remaining `engine/src` and `engine/test` file for the deleted headers and class names.
2. **The engine is started with the old signaling environment variables by something still in the repo** (a script, a wrapper, a doc example). Expected: none sends them; the engine ignores them anyway. Pinned in Task 1 (`test_main_has_no_signaling_wiring`) and Task 3 (grep over `scripts/`, `tests/`, `engine/*.ps1`).
3. **CI loses its C++ test run.** Expected: the `build-engine` job still runs `engine_tests.exe` with no `--gtest_filter` and fails the job on a non-zero exit. Pinned in Task 2 (`test_ci_runs_unfiltered_engine_suite_without_relay`).
4. **`npm install` after removing the workspace drops or bumps packages other workspaces use.** Expected: only the removed workspace and packages used by nothing else leave `package-lock.json`. Checked in Task 4 Step 5 with `git diff --stat` and the four JS test suites.
5. **A deleted verifier is still referenced by a kept test, script or doc.** Expected: no dangling reference. Pinned in Task 3 (`test_removed_verifier_files_are_gone` and the reference grep) and Task 5 (doc grep).

---

## File Structure

| File | Responsibility |
|---|---|
| `tests/test_engine_sources.py` (new) | Static guard (runs on macOS): removed engine files are gone, nothing in `engine/` references them, CMake/vcpkg carry no signaling dependencies. |
| `engine/src/main.cpp`, `engine/CMakeLists.txt`, `engine/vcpkg.json` | Engine wiring without the public signaling bridge. |
| `.github/workflows/build.yml` | `build-engine` job: configure, build, run `engine_tests.exe` directly, stage artifact. |
| `tests/test_build_files.py` | CI and wrapper assertions updated for the above. |
| `scripts/verify_all.py` | Kept: the one-command automated check (pytest, core, ui, web, export), without the relay. |
| `engine/verify-all.ps1`, `engine/test.ps1` | Kept: `verify-all.ps1` runs `scripts/verify_all.py`; `test.ps1` launches the engine by hand. |

Deleted in this plan: `engine/src/{signaling_client,public_signaling}.{h,cpp}`, `engine/test/{test_signaling_client,test_public_signaling}.cpp`, `engine/test/signaling_test_utils.h`, `engine/test/tls/`, `scripts/{verify_engine_cutover,verify_python_orchestration,verify_frontend_cutover,verify_lib,measure_engine_cutover}.py`, `engine/{verify-engine-cutover,verify-python-orchestration,verify-frontend-cutover,measure-engine-cutover}.ps1`, `engine/test/{cutover_metrics_page,python_orchestration_verifier}.html`, `engine/test/README.md`, `engine/test/README_engine_cutover.md`, `engine/test/README_python_orchestration.md`, their tests, and `infra/vps/signaling/`.

---

## Before Task 1

```bash
uv sync && npm install
uv run pytest tests/ apps/desktop/ -q
npm run test:core && npm run test:ui && npm test -w apps/web && npm run test:signaling
```

Expected: everything passes. If something already fails, report it before starting. `tests/test_verify_lib.py::test_submit_file_confirmation_rejects_answering_the_same_prompt_twice` is occasionally flaky; rerun it alone before calling it a failure.

---

### Task 1: Remove the engine's public signaling client

**Files:**
- Create: `tests/test_engine_sources.py`
- Delete: `engine/src/signaling_client.h`, `engine/src/signaling_client.cpp`, `engine/src/public_signaling.h`, `engine/src/public_signaling.cpp`, `engine/test/test_signaling_client.cpp`, `engine/test/test_public_signaling.cpp`, `engine/test/signaling_test_utils.h`
- Modify: `engine/src/main.cpp`, `engine/CMakeLists.txt`, `engine/vcpkg.json`, `engine/test/test_peer_session.cpp` (one comment)

**Interfaces:**
- Consumes: nothing.
- Produces: `engine.exe` with no signaling environment variables; `engine_core` without `websocketpp`/`asio`/`crypt32`.

- [ ] **Step 1: Write the failing guard test**

Create `tests/test_engine_sources.py`:

```python
from pathlib import Path

import pytest

ENGINE = Path(__file__).parent.parent / "engine"

REMOVED = (
    "src/signaling_client.h",
    "src/signaling_client.cpp",
    "src/public_signaling.h",
    "src/public_signaling.cpp",
    "test/test_signaling_client.cpp",
    "test/test_public_signaling.cpp",
    "test/signaling_test_utils.h",
)

# Names that only the deleted files defined or used.
DEAD_NAMES = (
    "signaling_client.h",
    "public_signaling.h",
    "signaling_test_utils.h",
    "SignalingClient",
    "PublicSignalingBridge",
    "ENGINE_SIGNALING",
    "ENGINE_SESSION",
    "ENGINE_PUBLIC_ICE_SERVERS",
    "websocketpp",
)


def _source_files():
    for folder in ("src", "test"):
        for path in sorted((ENGINE / folder).rglob("*")):
            if path.suffix in {".cpp", ".h"}:
                yield path


@pytest.mark.parametrize("relative", REMOVED)
def test_removed_engine_file_is_gone(relative):
    assert not (ENGINE / relative).exists(), relative


def test_nothing_in_engine_sources_references_the_removed_code():
    for path in _source_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for name in DEAD_NAMES:
            assert name not in text, f"{path.relative_to(ENGINE)} still mentions {name}"


def test_cmake_and_vcpkg_carry_no_signaling_dependencies():
    cmake = (ENGINE / "CMakeLists.txt").read_text(encoding="utf-8")
    vcpkg = (ENGINE / "vcpkg.json").read_text(encoding="utf-8")
    for token in ("signaling", "websocketpp", "find_package(asio", "asio::asio",
                  "ASIO_STANDALONE", "crypt32"):
        assert token not in cmake, token
    for token in ("websocketpp", '"asio"'):
        assert token not in vcpkg, token


def test_main_keeps_the_local_whep_wiring():
    main = (ENGINE / "src" / "main.cpp").read_text(encoding="utf-8")
    assert "ENGINE_WHEP_CAPABILITY_SECRET" in main
    assert "ENGINE_LOCAL_ICE_SERVERS" in main
    assert "WhepHandler" in main


def test_cmake_keeps_the_dependencies_local_whep_needs():
    cmake = (ENGINE / "CMakeLists.txt").read_text(encoding="utf-8")
    for token in ("libdatachannel", "nlohmann_json", "httplib", "OpenSSL",
                  "ws2_32.lib", "engine_tests"):
        assert token in cmake, token
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_engine_sources.py -q`
Expected: the `REMOVED` cases, the reference scan and the CMake/vcpkg test fail (`src/signaling_client.h` exists, `main.cpp` mentions `SignalingClient`, `websocketpp` is in CMake). The two `keeps_*` tests pass.

- [ ] **Step 3: Delete the sources and tests**

```bash
git rm -q engine/src/signaling_client.h engine/src/signaling_client.cpp \
  engine/src/public_signaling.h engine/src/public_signaling.cpp \
  engine/test/test_signaling_client.cpp engine/test/test_public_signaling.cpp \
  engine/test/signaling_test_utils.h
```

- [ ] **Step 4: Edit `engine/src/main.cpp`**

1. Delete the two include lines `#include "public_signaling.h"` and `#include "signaling_client.h"`.

2. Replace the usage message

```cpp
        std::cerr << "Usage: engine.exe <instance_name> <scrcpy_port>\n"
                     "Environment: ENGINE_WHEP_CAPABILITY_SECRET, "
                     "ENGINE_LOCAL_ICE_SERVERS, ENGINE_SIGNALING_URL, "
                     "ENGINE_SIGNALING_TOKEN, ENGINE_SESSION, "
                     "ENGINE_PUBLIC_ICE_SERVERS\n";
```

with

```cpp
        std::cerr << "Usage: engine.exe <instance_name> <scrcpy_port>\n"
                     "Environment: ENGINE_WHEP_CAPABILITY_SECRET, "
                     "ENGINE_LOCAL_ICE_SERVERS\n";
```

3. Delete this whole block (from the `std::unique_ptr<PublicSignalingBridge>` line through the closing brace of the `if (!signalingUrl.empty())` body) and the blank line after it:

```cpp
        std::unique_ptr<PublicSignalingBridge> publicBridge;
        // Destroy the transport first on exceptional exits so its callback
        // cannot outlive the bridge object it captures.
        std::unique_ptr<SignalingClient> signaling;
        std::string signalingUrl = GetEnvOrEmpty("ENGINE_SIGNALING_URL");
        if (!signalingUrl.empty()) {
            std::string signalingToken = GetEnvOrEmpty("ENGINE_SIGNALING_TOKEN");
            std::string session = GetEnvOrEmpty("ENGINE_SESSION");
            if (session.empty()) session = instanceName;
            signaling = std::make_unique<SignalingClient>(
                signalingUrl, session, "engine", signalingToken);
            auto publicIceServers = ParseCommaSeparatedList(GetEnvOrEmpty("ENGINE_PUBLIC_ICE_SERVERS"));
            publicBridge = std::make_unique<PublicSignalingBridge>(*signaling, registry, publicIceServers, inputRouter);
            publicBridge->Start();
        }
```

4. Delete the line `        if (signaling) signaling->Disconnect();`.

`ParseCommaSeparatedList`, `GetEnvOrEmpty`, `engine_config.h` and everything else stay.

- [ ] **Step 5: Edit `engine/CMakeLists.txt`**

1. Replace the comment inside the `if(MSVC)` block so it no longer names websocketpp (keep the flag; libdatachannel and the standard library headers read `__cplusplus`):

```cmake
if(MSVC)
  # MSVC's __cplusplus macro is stuck at 199711L unless this is set, no
  # matter what /std: flag is active; library feature detection reads it.
  add_compile_options(/Zc:__cplusplus)
endif()
```

2. Delete `find_package(websocketpp CONFIG REQUIRED)` and `find_package(asio CONFIG REQUIRED)`.
3. In `add_library(engine_core ...)` delete `  src/signaling_client.cpp` and `  src/public_signaling.cpp`.
4. Delete `target_compile_definitions(engine_core PUBLIC ASIO_STANDALONE)`.
5. In `target_link_libraries(engine_core PUBLIC ...)` delete `  websocketpp::websocketpp`, `  asio::asio` and `  crypt32.lib`.
6. In `add_executable(engine_tests ...)` delete `  test/test_signaling_client.cpp` and `  test/test_public_signaling.cpp`.

- [ ] **Step 6: Edit `engine/vcpkg.json`**

Write the whole file:

```json
{
  "name": "emuctrl-engine",
  "version": "0.1.0",
  "dependencies": [
    { "name": "libdatachannel", "features": ["srtp"] },
    "cpp-httplib",
    "gtest",
    "nlohmann-json"
  ]
}
```

- [ ] **Step 7: Reword the stale comment in `engine/test/test_peer_session.cpp`**

Near line 35 the comment reads "browser/mobile WHEP or VPS-signaling client". Replace that phrase so the comment says "browser/mobile WHEP client" and nothing else in the comment changes.

- [ ] **Step 8: Run the guard test and the Python suite**

Run: `uv run pytest tests/test_engine_sources.py -q`
Expected: all pass.

Run: `rtk proxy grep -rnE "signaling|Signaling|SIGNALING|websocketpp|ENGINE_SESSION" engine/src engine/test --include="*.cpp" --include="*.h"`
Expected: no output. (Docs and scripts under `engine/` are Tasks 3 and 5.)

Run: `uv run pytest tests/ apps/desktop/ -q`
Expected: all pass. State in the report: "C++ compilation NOT verified locally; validated only by the build-engine workflow".

- [ ] **Step 9: Commit**

```bash
git add tests/test_engine_sources.py engine/src engine/test engine/CMakeLists.txt engine/vcpkg.json
git status --short | grep -v '^??'
git commit -m "refactor(engine): remove the public signaling client"
```

Check `git status --short` before committing: only paths under `engine/` and `tests/test_engine_sources.py` should be staged (deletions show as `D`).

---

### Task 2: CI runs the engine tests without the relay

**Files:**
- Modify: `.github/workflows/build.yml`
- Modify: `tests/test_build_files.py`

**Interfaces:**
- Consumes: Task 1 (the engine tests no longer need a relay or TLS fixtures).
- Produces: `build-engine` job steps: checkout, vcpkg cache, configure, build, **Run engine_tests**, stage, upload.

- [ ] **Step 1: Rewrite the CI test (failing first)**

In `tests/test_build_files.py` replace the whole function `test_ci_runs_unfiltered_engine_suite_with_node_relay` with:

```python
def test_ci_runs_unfiltered_engine_suite_without_relay():
    text = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
    assert "engine_tests.exe" in text
    assert "--gtest_filter" not in text
    assert "LASTEXITCODE" in text
    for removed in ("infra/vps/signaling", "SIGNALING_TLS", "ENGINE_TEST_WSS_PORT",
                    "SSL_CERT_FILE", "signaling relay"):
        assert removed not in text, removed


def test_ci_runs_engine_tests_between_build_and_staging():
    text = (REPO_ROOT / ".github" / "workflows" / "build.yml").read_text()
    build = text.index("cmake --build engine")
    tests = text.index("engine_tests.exe")
    stage = text.index("Stage engine.exe")
    assert build < tests < stage
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_build_files.py -q`
Expected: `test_ci_runs_unfiltered_engine_suite_without_relay` fails on `infra/vps/signaling`.

- [ ] **Step 3: Edit `.github/workflows/build.yml`**

In the `build-engine` job, delete these three steps (the `actions/setup-node@v4` step with `node-version: '20'`, the `Install signaling relay dependencies` step, and the `Run complete engine_tests behind the Node signaling relay` step) and put this single step in their place, directly after the `Build` step and before `Stage engine.exe + runtime DLLs (flat)`:

```yaml
      - name: Run engine_tests
        run: |
          engine\build\Release\engine_tests.exe
          if ($LASTEXITCODE -ne 0) {
            throw "engine_tests.exe failed with exit code $LASTEXITCODE"
          }
        shell: pwsh
```

Leave the `build` job (it has its own `setup-node` for the web build) and every other step untouched. Do not add `--gtest_filter`.

- [ ] **Step 4: Run the tests and parse the YAML**

Run: `uv run pytest tests/test_build_files.py -q`
Expected: all pass except `test_engine_cutover_wrapper_forwards_the_complete_safety_contract`, which still passes at this commit (Task 3 deletes it).

Run: `uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/build.yml')); print('yaml ok')"`
Expected: `yaml ok`. If `yaml` is not importable under `uv run`, use `uv run --with pyyaml python -c ...`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/build.yml tests/test_build_files.py
git commit -m "ci(engine): run engine tests without the signaling relay"
```

---

### Task 3: Delete the cutover verifiers

These scripts need Windows, real ADB devices, browsers and interactive prompts, and every one of them starts the relay, sets removed environment variables, or asserts removed response fields. They are not runnable after Task 4 and duplicate the manual checklist. Delete them with their wrappers, fixtures and tests; keep `scripts/verify_all.py` (trimmed in Task 4) and `engine/test.ps1`.

**Files:**
- Delete: `scripts/verify_engine_cutover.py`, `scripts/verify_python_orchestration.py`, `scripts/verify_frontend_cutover.py`, `scripts/verify_lib.py`, `scripts/measure_engine_cutover.py`
- Delete: `engine/verify-engine-cutover.ps1`, `engine/verify-python-orchestration.ps1`, `engine/verify-frontend-cutover.ps1`, `engine/measure-engine-cutover.ps1`
- Delete: `engine/test/cutover_metrics_page.html`, `engine/test/python_orchestration_verifier.html`
- Delete: `tests/test_engine_cutover_verifier.py`, `tests/test_windows_verifier.py`, `tests/test_frontend_cutover_verifier.py`, `tests/test_measure_engine_cutover.py`, `tests/test_verify_lib.py`
- Modify: `tests/test_build_files.py`, `tests/test_engine_process.py`, `tests/test_engine_sources.py`, `pyproject.toml`, `requirements.txt`, `uv.lock`

**Interfaces:**
- Consumes: Task 2 (the CI no longer mentions the verifiers).
- Produces: no `verify_*`/`measure_*` scripts other than `scripts/verify_all.py`; `websockets` is no longer a dependency.

- [ ] **Step 1: Add the failing guard**

Append to `tests/test_engine_sources.py`:

```python
REPO = Path(__file__).parent.parent

REMOVED_VERIFIER_FILES = (
    "scripts/verify_engine_cutover.py",
    "scripts/verify_python_orchestration.py",
    "scripts/verify_frontend_cutover.py",
    "scripts/verify_lib.py",
    "scripts/measure_engine_cutover.py",
    "engine/verify-engine-cutover.ps1",
    "engine/verify-python-orchestration.ps1",
    "engine/verify-frontend-cutover.ps1",
    "engine/measure-engine-cutover.ps1",
    "engine/test/cutover_metrics_page.html",
    "engine/test/python_orchestration_verifier.html",
    "tests/test_engine_cutover_verifier.py",
    "tests/test_windows_verifier.py",
    "tests/test_frontend_cutover_verifier.py",
    "tests/test_measure_engine_cutover.py",
    "tests/test_verify_lib.py",
)


@pytest.mark.parametrize("relative", REMOVED_VERIFIER_FILES)
def test_removed_verifier_files_are_gone(relative):
    assert not (REPO / relative).exists(), relative


def test_kept_verification_entry_points_remain():
    assert (REPO / "scripts" / "verify_all.py").exists()
    assert (REPO / "engine" / "verify-all.ps1").exists()
    assert (REPO / "engine" / "test.ps1").exists()


def test_websockets_is_no_longer_a_dependency():
    for name in ("pyproject.toml", "requirements.txt"):
        assert "websockets" not in (REPO / name).read_text(encoding="utf-8").lower(), name
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_engine_sources.py -q`
Expected: the 16 `test_removed_verifier_files_are_gone` cases and `test_websockets_is_no_longer_a_dependency` fail; `test_kept_verification_entry_points_remain` passes.

- [ ] **Step 3: Delete the files**

```bash
git rm -q scripts/verify_engine_cutover.py scripts/verify_python_orchestration.py \
  scripts/verify_frontend_cutover.py scripts/verify_lib.py scripts/measure_engine_cutover.py \
  engine/verify-engine-cutover.ps1 engine/verify-python-orchestration.ps1 \
  engine/verify-frontend-cutover.ps1 engine/measure-engine-cutover.ps1 \
  engine/test/cutover_metrics_page.html engine/test/python_orchestration_verifier.html \
  tests/test_engine_cutover_verifier.py tests/test_windows_verifier.py \
  tests/test_frontend_cutover_verifier.py tests/test_measure_engine_cutover.py \
  tests/test_verify_lib.py
```

- [ ] **Step 4: Update the kept tests**

In `tests/test_build_files.py` delete the whole function `test_engine_cutover_wrapper_forwards_the_complete_safety_contract`.

In `tests/test_engine_process.py`, in `test_spawn_excludes_auth_token_and_preserves_engine_environment`, replace the removed variable with the one the engine still receives:

```python
    instance, captured = make_capturing_instance(
        env_overrides={"ENGINE_LOCAL_ICE_SERVERS": "stun:100.64.1.4:3478",
                        "FAKE_ENGINE_MODE": "ready"}
    )
    try:
        instance.start()
        assert captured["env"]["WINDOWCONTROL_PARENT_SENTINEL"] == "present"
        assert captured["env"]["ENGINE_LOCAL_ICE_SERVERS"] == "stun:100.64.1.4:3478"
        assert "AUTH_TOKEN" not in captured["env"]
```

(The rest of the test is unchanged.)

- [ ] **Step 5: Drop the `websockets` dependency**

In `pyproject.toml` delete the line `    "websockets>=14.0",  # scripts/verify_engine_cutover.py`. In `requirements.txt` delete the line `websockets>=14.0  # scripts/verify_engine_cutover.py`.

Run: `grep -rn "websockets" --include="*.py" src scripts tests apps`
Expected: no output (nothing imports it). If something does, stop and report it; do not remove the dependency.

Run: `uv lock && uv sync`
Expected: `uv.lock` loses the project's direct `websockets` requirement (the `specifier = ">=14.0"` line). The `websockets` package itself stays locked because `uvicorn[standard]` depends on it; that is correct, and `build/window_control.spec` still lists `uvicorn.protocols.websockets` as a hidden import. Do not remove it from the lock by hand.

- [ ] **Step 6: Confirm nothing dangling**

Run: `rtk proxy grep -rnE "verify_lib|measure_engine_cutover|verify_engine_cutover|verify_python_orchestration|verify_frontend_cutover|verify-engine-cutover|verify-python-orchestration|verify-frontend-cutover|measure-engine-cutover|cutover_metrics_page|python_orchestration_verifier" scripts tests engine/*.ps1 build .github src apps pyproject.toml requirements.txt package.json`
Expected: only hits inside `tests/test_engine_sources.py` (the guard's own file list). Docs are Task 5.

- [ ] **Step 7: Run the Python suite**

Run: `uv run pytest tests/ apps/desktop/ -q`
Expected: all pass (the deleted test files account for the lower count).

- [ ] **Step 8: Commit**

```bash
git add tests/test_engine_sources.py tests/test_build_files.py tests/test_engine_process.py pyproject.toml requirements.txt uv.lock
git status --short | grep -v '^??'
git commit -m "chore(scripts): remove the cutover verifiers and their wrappers"
```

Check `git status --short`: the deletions staged by `git rm` plus the seven paths above, nothing else.

---

### Task 4: Remove the relay and trim `verify_all.py`

**Files:**
- Delete: `infra/vps/signaling/` (tracked files), `engine/test/tls/`
- Modify: `package.json`, `package-lock.json`, `scripts/verify_all.py`, `tests/test_engine_sources.py`

**Interfaces:**
- Consumes: Task 3 (nothing runs the relay any more).
- Produces: `infra/` holds no tracked files besides what the user keeps untracked; root `package.json` has no `infra/vps/signaling` workspace and no `test:signaling` script.

- [ ] **Step 1: Add the failing guard**

Append to `tests/test_engine_sources.py`:

```python
def test_signaling_relay_and_its_fixtures_are_gone():
    import subprocess

    tracked = subprocess.run(
        ["git", "ls-files", "infra", "engine/test/tls"],
        cwd=REPO, capture_output=True, text=True, check=True,
    ).stdout.split()
    assert tracked == [], tracked


def test_root_package_has_no_signaling_workspace_or_script():
    import json

    package = json.loads((REPO / "package.json").read_text(encoding="utf-8"))
    assert "infra/vps/signaling" not in package["workspaces"]
    assert "test:signaling" not in package["scripts"]


def test_verify_all_does_not_run_or_install_the_relay():
    text = (REPO / "scripts" / "verify_all.py").read_text(encoding="utf-8")
    for removed in ("test:signaling", "signaling", "Signaling", "jose", "infra"):
        assert removed not in text, removed
    assert "pair.html" in text
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_engine_sources.py -q`
Expected: the three new tests fail.

- [ ] **Step 3: Delete the relay and the TLS fixtures**

```bash
git rm -r -q infra/vps/signaling engine/test/tls
```

`git rm` removes tracked files only. An untracked `infra/vps/signaling/node_modules/` may remain on disk; leave it and report it.

- [ ] **Step 4: Edit `package.json`**

Delete `    "infra/vps/signaling"` from `workspaces` (and the comma after the previous entry, so `"apps/mobile"` is last) and delete the line `    "test:signaling": "npm test -w infra/vps/signaling",`. The result:

```json
{
  "name": "emuctrl",
  "private": true,
  "workspaces": [
    "packages/*",
    "apps/web",
    "apps/mobile"
  ],
  "scripts": {
    "test:core": "npm test -w packages/core",
    "test:ui": "npm test -w packages/ui",
    "generate:brand": "node scripts/generate-brand-assets.mjs"
  },
  "devDependencies": {
    "png-to-ico": "^3.0.2",
    "sharp": "^0.35.4"
  }
}
```

- [ ] **Step 5: Regenerate the lock file**

Run: `npm install`
Run: `git diff --stat package-lock.json`
Expected: only removals of the `infra/vps/signaling` workspace entries and of packages no other workspace uses (for example `jose`); no version bumps of packages used by `packages/*`, `apps/web`, `apps/mobile`. If the diff contains version changes for packages that other workspaces use, stop and report the diff stat and the changed package names; do not commit it and do not discard it with `git checkout`.

Run: `npm run test:core && npm run test:ui && npm test -w apps/web`
Expected: all pass (92, 80, 16).

- [ ] **Step 6: Trim `scripts/verify_all.py`**

1. In the module docstring, change "backend, frontend, desktop, and signaling checks" to "backend, frontend and desktop checks".
2. In `steps`, rename `"TypeScript Core Session & Signaling"` to `"TypeScript Core Session & Pairing"`, and delete the last tuple (the `"VPS WebRTC Signaling Relay Tests"` step with `["npm", "run", "test:signaling"]`).
3. Delete the whole block that starts with the comment `# Ensure signaling relay dependencies are installed if not already present` and ends with the closing of `if not has_jose or not has_ws:` (the `vps_dir`, `has_jose`, `has_ws` lines and the install attempt with its printing).

Everything else stays (including the `pair.html` / `login.html` export checks added earlier).

- [ ] **Step 7: Run the guard, the suites and the script**

Run: `uv run pytest tests/test_engine_sources.py -q` → all pass.
Run: `uv run pytest tests/ apps/desktop/ -q` → all pass.
Run: `rtk proxy grep -rnE "infra/vps|test:signaling|signaling" package.json scripts .github build pyproject.toml`
Expected: no output.

- [ ] **Step 8: Commit**

```bash
git add tests/test_engine_sources.py package.json package-lock.json scripts/verify_all.py
git status --short | grep -v '^??'
git commit -m "chore(infra): remove the signaling relay and its test fixtures"
```

---

### Task 5: Documentation

**Files:**
- Delete: `engine/test/README.md`, `engine/test/README_engine_cutover.md`, `engine/test/README_python_orchestration.md`
- Modify: `engine/test/README_e2e.md`, `engine/BUILD_WINDOWS.md`, `CHECKLIST.md`, `docs/WINDOWS_MANUAL_VALIDATION.md`, `docs/TROUBLESHOOTING.md`, `docs/UI_UX_REDESIGN_SPEC.md`, `README.md`, `MEMORY.md`, `CHANGELOG.md`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: docs that describe only what exists: local/Tailscale access with pairing, the engine tests, `scripts/verify_all.py`.

Read each file before editing. For each hunk below delete or rewrite only what mentions the removed pieces; keep the rest of the document intact.

- [ ] **Step 1: Write the failing doc guard**

Append to `tests/test_engine_sources.py`:

```python
LIVE_DOCS = (
    "README.md",
    "CHECKLIST.md",
    "MEMORY.md",
    "docs/WINDOWS_MANUAL_VALIDATION.md",
    "docs/TROUBLESHOOTING.md",
    "engine/BUILD_WINDOWS.md",
    "engine/test/README_e2e.md",
)

DEAD_DOC_TERMS = (
    "infra/vps/signaling",
    "test:signaling",
    "VPS_SIGNALING_URL",
    "ENGINE_SIGNALING",
    "ENGINE_TEST_WSS_PORT",
    "verify-engine-cutover",
    "verify-python-orchestration",
    "verify-frontend-cutover",
    "measure-engine-cutover",
    "verify_engine_cutover",
    "verify_python_orchestration",
    "verify_frontend_cutover",
    "websocketpp",
    "coturn",
    "TURN_",
)


@pytest.mark.parametrize("relative", LIVE_DOCS)
def test_live_docs_do_not_describe_removed_infrastructure(relative):
    text = (REPO / relative).read_text(encoding="utf-8")
    for term in DEAD_DOC_TERMS:
        assert term not in text, f"{relative} still mentions {term}"


@pytest.mark.parametrize("relative", (
    "engine/test/README.md",
    "engine/test/README_engine_cutover.md",
    "engine/test/README_python_orchestration.md",
))
def test_removed_engine_docs_are_gone(relative):
    assert not (REPO / relative).exists(), relative


@pytest.mark.parametrize("relative", LIVE_DOCS)
def test_live_docs_do_not_link_to_removed_docs(relative):
    text = (REPO / relative).read_text(encoding="utf-8")
    for link in ("test/README.md", "README_engine_cutover", "README_python_orchestration"):
        assert link not in text, f"{relative} still links to {link}"


def test_memory_has_no_signaling_lesson_and_changelog_records_the_release():
    assert "Signaling Tests" not in (REPO / "MEMORY.md").read_text(encoding="utf-8")
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    assert "## [v3.2.0]" in changelog
    assert changelog.index("## [v3.2.0]") < changelog.index("## [v3.1.2]")
```

- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/test_engine_sources.py -q`
Expected: failures name every doc that still carries a dead term; the three `removed_engine_docs` cases fail.

- [ ] **Step 3: Delete the three docs that are wholly about removed pieces**

```bash
git rm -q engine/test/README.md engine/test/README_engine_cutover.md engine/test/README_python_orchestration.md
```

- [ ] **Step 4: Edit the kept docs**

- `engine/test/README_e2e.md`: remove the public-path disclaimer (around lines 26–27), the relay/`gtest_filter` instructions (around lines 52 and 58–66) and the stale signaling environment variables (around lines 113–123); keep the local WHEP walkthrough and the manual steps after it. Where it linked to a deleted README, delete the link sentence.
- `engine/BUILD_WINDOWS.md`: update the CI summary (around lines 14–16) to "configure, build, run `engine_tests.exe`"; remove `websocketpp`/`asio` from the dependency list (around lines 50–53); delete the websocketpp pin section (around lines 71–86), the `signaling_client` mentions (around lines 90 and 102), the relay requirement (around lines 109–110) and the VPS/coturn fourth-argument section (around lines 115–120).
- `CHECKLIST.md`: remove the verifier rows from the quick-reference table except `verify-all.ps1` / `verify_all.py`; remove the `VPS_SIGNALING_URL` instructions (around lines 36–44), item 2.8 (relay, around line 71), and the gtest-filter / live-relay sections 4.2–4.3 (around lines 114–137, including the TLS environment setup), replacing them with one line: "Run `engine\build\Release\engine_tests.exe` (the complete suite; CI runs the same)". Remove remaining verifier commands (around lines 54–55, 80–81, 171).
- `docs/WINDOWS_MANUAL_VALIDATION.md`: remove `verify-frontend-cutover` and signaling-test mentions and counts (around lines 6, 15–20, 31, 89, 163) so it refers only to `.\engine\verify-all.ps1`; delete the `VPS_SIGNALING_URL` plus Supabase variable instructions (around line 59). Add one step to its pairing-relevant part: "On the PC click Pair device in the launcher; pair a phone and a browser; reload and confirm no code is asked; remove the device and confirm the client returns to pairing; stream from a device on the LAN without Tailscale and from a Tailscale device."
- `docs/TROUBLESHOOTING.md`: edit the `engine_tests` / relay paragraph (around lines 117–121) so it says the engine tests need no relay; remove the link to `engine/test/README.md` (line 118; that file is deleted) and keep the links to `engine/BUILD_WINDOWS.md` and `engine/test/README_e2e.md`.
- `engine/BUILD_WINDOWS.md` line 110 points at `engine/test/README.md`: delete that pointer along with the relay requirement it belongs to.
- `docs/UI_UX_REDESIGN_SPEC.md`: at the "Remote WAN via VPS and Coturn" line (around line 28) and the "VPS Relay Connectivity" item (around line 189) add the words "(removed; access is LAN and Tailscale only)" instead of rewriting the historical spec.
- `README.md`: delete the `infra/vps/signaling` line from the file-structure tree (around line 171) and any other line mentioning the removed directories.
- `MEMORY.md`: delete the "Signaling Tests" learning (around line 9). The file then holds four lessons.
- `CHANGELOG.md`: the newest section is `## [v3.1.2] — September 10, 2026`, but the version is already 3.2.0 with no entry. Insert a new section directly above it (after the `---` line under the intro), in the file's existing style:

```markdown
## [v3.2.0] — October 1, 2026

EmuCtrl is now local-only: it answers devices on your network or Tailscale, and each device is paired once with a code shown on the PC.

### What's New
- **Device Pairing**: Click **Pair device** in the host window, enter the 6-digit code on the phone or browser, and the device is remembered. Remove it from **Paired Devices** to revoke access.
- **Local-Only Access**: Requests from outside the local network and Tailscale are refused.

### Removed
- **Accounts and Public Access**: Sign-in, the internet relay, TURN and the public tunnel are gone, along with the engine's signaling client, the cutover verifier scripts and the relay's infrastructure code.

---
```

- [ ] **Step 5: Run the guard and the greps**

Run: `uv run pytest tests/test_engine_sources.py -q` → all pass.
Run: `rtk proxy grep -rnE "infra/vps/signaling|test:signaling|VPS_SIGNALING_URL|verify-engine-cutover|verify-python-orchestration|verify-frontend-cutover|measure-engine-cutover|websocketpp" README.md CHECKLIST.md MEMORY.md docs/WINDOWS_MANUAL_VALIDATION.md docs/TROUBLESHOOTING.md engine`
Expected: no output.

Run: `uv run pytest tests/ apps/desktop/ -q` → all pass.

- [ ] **Step 6: Commit**

```bash
git add tests/test_engine_sources.py README.md CHECKLIST.md MEMORY.md CHANGELOG.md docs/WINDOWS_MANUAL_VALIDATION.md docs/TROUBLESHOOTING.md docs/UI_UX_REDESIGN_SPEC.md engine
git status --short | grep -v '^??'
git commit -m "docs: remove signaling relay and cutover verifier documentation"
```

Check `git status --short`: the three deleted READMEs plus the modified docs above, nothing else.

---

## Not in this plan

- Removing `PeerKind::Public`, `PeerRegistry::Adopt`, `HasPublicPeer` and the `public_peer` field of `/admin/health`. It is a Python-visible contract (`src/server/engine_admin.py`, `tests/test_engine_admin.py`, `tests/fixtures/fake_admin_server.py`, `tests/test_engine_runtime.py`, `engine/test/test_admin_handler.cpp`, `test_input_router.cpp`, `test_peer_registry.cpp`); do it as its own change in both languages at once.
- Moving `engine/vcpkg-configuration.json` to a newer vcpkg baseline now that `websocketpp` is gone; needs a CI build to confirm.
- Dropping the `/Zc:__cplusplus` MSVC flag; kept deliberately.
- Fixes from the Plan A final review that were parked (loose Host port parsing, `?token=` scope, burned-code message in the launcher).
- Deleting `infra/vps/signaling/node_modules` and the local `infra/terraform` state, which are untracked local files.
