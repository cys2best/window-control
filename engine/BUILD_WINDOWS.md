# Building engine.exe on Windows

CI builds the engine and runs `engine_tests.exe` (the `build-engine` job in
`.github/workflows/build.yml`, on `v*` tags and manual dispatch). Local Windows
builds follow the steps below.

## Option A: GitHub Actions (no local Windows box needed)

`.github/workflows/build.yml` has a `build-engine` job (Windows runner,
runs on `v*` tags and on manual dispatch — run it from the Actions tab, "Run workflow").
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

`engine/vcpkg-configuration.json` pins the vcpkg baseline; moving it is deferred because it needs a CI build to confirm.

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

## After it builds

1. Run the engine tests (no scrcpy or network needed):
   ```
   engine\build\Release\engine_tests.exe
   ```
2. Follow `engine/test/README_e2e.md` for the full local end-to-end run
   against a real device.

## If you hit a build error not covered here

Report back the exact error (file:line, compiler message) rather than
guessing at a fix; the fastest path is diagnosing the real error.
