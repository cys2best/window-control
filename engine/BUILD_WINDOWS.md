# Building engine.exe on Windows

This project has never been compiled — all of Tasks 5-9 were written and
reviewed on a Darwin host with no Windows toolchain available. Everything
below is verified by reading `CMakeLists.txt`/`vcpkg.json` and cross-
checking against the code, not by a successful build. Expect to debug real
compile errors on the first attempt; this doc gets you to that point with
the least wasted time.

## Option A: GitHub Actions (no local Windows box needed)

`.github/workflows/build.yml` has a `build-engine` job (Windows runner,
`workflow_dispatch`-only — run it from the Actions tab, "Run workflow").
It configures with vcpkg, builds `Release`, runs `engine_tests.exe`, and
uploads `engine.exe` as a workflow artifact. This is the
fastest way to get a real compiler's verdict on this code without owning
Windows hardware — start here, then use Option B below only if you need to
debug locally or run the real-device gates.

## Option B: Local Windows machine

## Prerequisites

- **Visual Studio 2022** (Community is fine) with the "Desktop development
  with C++" workload — gives you MSVC, the Windows SDK, and CMake tools.
- **vcpkg**, cloned and bootstrapped:
  ```
  git clone https://github.com/microsoft/vcpkg
  .\vcpkg\bootstrap-vcpkg.bat
  ```
- **CMake >= 3.24** (bundled with VS2022, or install standalone).

## Configure

From the repo root:

```powershell
# If using standalone or custom vcpkg:
cmake -S engine -B engine\build `
  -DCMAKE_TOOLCHAIN_FILE="<path-to-vcpkg>\scripts\buildsystems\vcpkg.cmake" `
  -DVCPKG_TARGET_TRIPLET=x64-windows

# If using Visual Studio 2022 Build Tools:
cmake -S engine -B engine\build `
  -DCMAKE_TOOLCHAIN_FILE="C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\vcpkg\scripts\buildsystems\vcpkg.cmake" `
  -DVCPKG_TARGET_TRIPLET=x64-windows
```

`vcpkg.json` (manifest mode) pulls in `libdatachannel`, `cpp-httplib`,
`gtest`, `nlohmann-json` automatically on first configure — expect this step to take a while (libdatachannel has a large dependency
tree: OpenSSL, usrsctp, etc.). No manual `vcpkg install` needed.

## Build

```
cmake --build engine\build --config Release
```

Or build a single target while iterating:

```
cmake --build engine\build --target engine_core --config Release
cmake --build engine\build --target engine_tests --config Release
cmake --build engine\build --target engine --config Release
```

## Known friction points (read before debugging blind)

1. **`ScrcpyControlClient::RequestIdr()` and the RTP-timestamp fix in
   `peer.cpp` were added in the final review's fix wave (commit
   `27c2be2`) and have never compiled.** If the build fails specifically
   in `peer.cpp` or `scrcpy_control.cpp`/`.h`, check those files first —
   they're the newest, least-scrutinized-by-compiler code in the tree.

2. **All C++ across Tasks 5-9 (`scrcpy_video.*`, `scrcpy_control.*`,
   `peer.*`, `main.cpp`) is equally unverified.**
   Don't assume earlier files are "more trustworthy" than later ones —
   none of it has seen a compiler yet.

## After it builds

1. Run the engine tests (no scrcpy, relay or network needed):
   ```
   engine\build\Release\engine_tests.exe
   ```
2. Follow `engine/test/README_e2e.md` for the full local end-to-end run
   against a real device.

## If you hit a build error not covered here

Report back the exact error (file:line, compiler message) rather than
guessing at a fix — this is genuinely first-contact-with-a-compiler code,
and the fastest path is diagnosing the real error, not pattern-matching
against what "should" be wrong.
