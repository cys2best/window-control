# Remote Engine Compatibility & TURN Backend Verification

Date: 2026-10-02
Status: Build configuration & overlay ready for Windows CI verification.

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

## Verification Status

- [x] Python build guards verify overlay existence, `USE_NICE=ON`, and CI workflow cache keys (`test_engine_overlay_selects_libnice`).
- [ ] Windows native compilation: Requires Windows runner / CI (`engine/` only compiles on Windows). Native C++ compilation and `ctest` DLL closure must run in the Windows CI environment.
- [ ] TURN/TLS live connection test: Task 11 validates end-to-end media playback over TURN/TLS.
