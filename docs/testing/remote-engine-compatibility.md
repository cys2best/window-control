# Remote Engine Compatibility & TURN Backend Verification

Date: 2026-10-09
Status: Windows workflow run 37877480719 passed at
`dcd002cfeed651e3dd64eea8624044747f7eb54f`: 124 native tests in the build tree and
from the staged artifact, the compiler/runtime gate, the staged host smoke and
the installer build. See the
[result matrix](results/shared-remote/dcd002cfeed651e3dd64eea8624044747f7eb54f-matrix.json)
for artifact IDs and for one unresolved intermittent native crash. Device media
remains untested.

## Background & Rationale

`emuctrl-engine` uses `libdatachannel` for WebRTC peer connections. By default, upstream `libdatachannel` builds with `libjuice` as its ICE backend. `libjuice` supports standard UDP STUN/TURN, but does NOT support TURN over TCP or TURN over TLS (TURNS), which are strictly required when UDP traffic is blocked or symmetric NAT prevents direct P2P connections.

To support TURN fallback across restrictive firewalls and cellular networks:
1. An overlay port is created at `engine/ports/libdatachannel`.
2. The overlay sets `-DUSE_NICE=ON` and `-DUSE_JUICE=OFF` during dependency configuration.
3. `libnice` is linked as the ICE backend for `libdatachannel`.
4. CI and local Windows builds pass `-DVCPKG_OVERLAY_PORTS=engine/ports`.
5. Transitive runtime DLLs (including `nice.dll`, `glib-2.0-0.dll`, etc.) are packaged alongside `engine.exe`.

## Build & Overlay Configuration

- **Overlay Port Location**: `engine/ports/libdatachannel`
- **vcpkg Manifest**: `engine/ports/libdatachannel/vcpkg.json`
- **Portfile**: `engine/ports/libdatachannel/portfile.cmake` with `-DUSE_NICE=ON` and `-DUSE_JUICE=OFF`
- **CI Workflow**: `.github/workflows/build.yml` specifies `-DVCPKG_OVERLAY_PORTS=engine/ports` and incorporates `engine/ports/**` into the vcpkg cache key.

## Verified Build Inputs

- The overlay preserves libdatachannel **0.21.1**. Its source is the official
  [v0.21.1 tag](https://github.com/paullouisageneau/libdatachannel/releases/tag/v0.21.1),
  annotated tag `dbafaa502bd163eb14fd140bc1a8388399bab56a`, targeting commit
  `898bdffe7340134f0891633cc7a6f6d2132c31c3`.
- The [official archive](https://github.com/paullouisageneau/libdatachannel/archive/v0.21.1.tar.gz)
  SHA512 was independently downloaded and verified as
  `67119f6d1280593696f71dc550ceba1076066d0f55ebf10b527bf1c75e5e9571be18e5d9732e43a2aa4d5106a49a0676e70938d35d3eb4005cf17612f8836c52`.
  This matches both the Windows download and the official vcpkg 0.21.1 port,
  rather than accepting a hash from the failure log alone.
- Port snapshot: [`f5218e93bae8971d509fd04910f9778004e58bce`](https://api.github.com/repos/microsoft/vcpkg/git/trees/f5218e93bae8971d509fd04910f9778004e58bce),
  recorded in the registry's [version history](https://github.com/microsoft/vcpkg/blob/ec12d917a85839741f8345905f71b3e7f56d9ddc/versions/l-/libdatachannel.json).
  The overlay restores the matching upstream patches and system usrsctp setup,
  replaces libjuice with libnice, and applies the snapshot's supplied
  `fix_srtp.patch` to select vcpkg's `libSRTP` CONFIG package. All four patch
  files match upstream bytes; patch application was checked against the archive
  with vcpkg's whitespace-tolerant Git patch behavior. The UWP patch is conditional.
- CI scripts are checked out at **`617ef1c0c422737d117eda00a471ea1be5bad088`**,
  the full revision observed in run 37001202701. This revision built libnice
  **0.1.22**, GLib **2.80.0#1**, libsrtp **2.5.0#1**, and OpenSSL **3.4.1**
  before the libdatachannel download failed. It is a measured scripts revision,
  **not a successful complete native build**. The registry baseline remains
  `ec12d917a85839741f8345905f71b3e7f56d9ddc`.
- The cache includes the scripts revision, manifest, registry configuration, and
  overlay contents, and stores the actual manifest install directory
  `engine/build/vcpkg_installed` plus `.vcpkg/downloads`. Broad restore keys were
  removed to avoid restoring an install tree from different build inputs.
- CI acquires pkg-config for the upstream libnice/GLib find modules. It copies
  all resolved `x64-windows/bin/*.dll` files beside both Release executables
  before testing, then preserves the existing flat installer artifact. This
  supplements `TARGET_RUNTIME_DLLS`, which cannot discover the full closure of
  libnice's private `UNKNOWN` imported target.

## Runtime and installer compatibility

The October 4 installer job produced `EmuCtrlInstaller.exe` while upload/release
steps requested `WindowControlInstaller.exe`; the artifact API had no installer.
Both consumers now request the actual filename and upload fails on missing output.
The retained GitHub artifact name is `WindowControlInstaller`.

The prior engine used MSVC tools `14.51.36231` while the workflow's `vs/17`
download bundled runtime `14.44.35211.0`. This is a measured version mismatch,
not a reproduced runtime crash. The current download uses Microsoft's
[latest supported v14 x64 alias](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist/).
Packaging records the actual CMake compiler's MSVC tool-directory version and
the downloaded runtime's PE FileVersion, and fails if the runtime is older or
either version is unknown. The installer compares the installed x64 registry
version with the bundled runtime; `Installed=1` alone no longer skips upgrades.
An executable Inno harness covers missing/older/equal/newer/unknown versions.
Its first two Windows runs failed to compile: code compiled inside `[Code]` may
not use `;` comments, and no line there may open with `[`. Both are now guarded
by `tests/test_build_files.py`.

Raw `engine-windows` contains third-party DLLs, but does not contain Microsoft's
`MSVCP140.dll`, `MSVCP140_ATOMIC_WAIT.dll`, `VCRUNTIME140.dll` or
`VCRUNTIME140_1.dll`. Install an x64 VC runtime at least as new as the recorded
tool version before launching a raw engine artifact. The installer bundles it.
Onedir staged launch with an installed runtime tests a different boundary from
installing the complete installer on a persistent clean Windows PC.

The Windows build job downloads a separately staged native-test artifact onto
a machine without the engine build tree, sets PATH to Windows System32 and
runs the unfiltered GoogleTest executable. It copies the actual PyInstaller
onedir output to a clean directory and launches the frozen host with the same
restricted PATH. Its smoke checks local API and bundled web export while remote
service is absent, then current-user DPAPI save/decryption/reuse after frozen
restart using a trusted local HTTPS registration fixture. This exercises neither
physical iPhone connectivity nor capture/media. It also does not install the
installer or demonstrate cross-machine/user persistence.

## Verification Status

- [x] Python build guards verify overlay existence, `USE_NICE=ON`, and CI workflow cache keys (`test_engine_overlay_selects_libnice`).
- [x] Source guards, independent archive SHA512 verification, and exact upstream patch/application checks cover the repaired inputs.
- [x] Prior Windows native compilation and 124 GoogleTests: run 37114034029 at `1c943f4`; libdatachannel 0.21.1#2 and libnice 0.1.22. This does not validate later checkpoints.
- [x] Current native/build/artifact and staged launch results: run 37877480719 at `dcd002c`; MSVC tools 14.51.36231, bundled runtime 14.51.36247.0, installer artifact 11593082083.
- [ ] Intermittent access violation in `InputRouter.EchoIsReflectedVerbatimOnSamePeer` (2 of 10 executions): cause not established.
- [ ] Install on a persistent clean Windows PC, verify its runtime upgrade and frozen DPAPI/LAN behavior; no persistent Windows test setup supplied.
- [ ] Actual iPhone direct/TURN connectivity and sustained full-HD media; no iPhone build, controlled service/network profiles or capacity evidence supplied. Synthetic assessor tests cannot establish playback.
