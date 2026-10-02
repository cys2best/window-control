# Remote Engine Compatibility & TURN Backend Verification

Date: 2026-10-02
Status: Windows run 37001202701 failed at archive integrity verification; source/build inputs repaired, Windows rerun pending.

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

## Verification Status

- [x] Python build guards verify overlay existence, `USE_NICE=ON`, and CI workflow cache keys (`test_engine_overlay_selects_libnice`).
- [x] Source guards, independent archive SHA512 verification, and exact upstream patch/application checks cover the repaired inputs.
- [ ] Windows native compilation: Requires Windows runner / CI (`engine/` only compiles on Windows). Native C++ compilation and `ctest` DLL closure must run in the Windows CI environment.
- [ ] After the Windows rerun, launch the staged executable from a clean directory and verify SRTP/OpenSSL peer APIs and libnice/GLib runtime DLL closure. Source checks do not prove these interfaces or standalone launch.
- [ ] TURN/TLS live connection test: Task 11 validates end-to-end media playback over TURN/TLS.
