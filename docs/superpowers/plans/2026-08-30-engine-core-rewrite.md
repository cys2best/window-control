# Engine Core Rewrite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rewrite `engine/` from a single-offerer, single-peer prototype into
a multi-peer, viewer-as-offerer WebRTC engine that serves WHEP locally,
speaks raw-SDP signaling on the VPS relay, supports on-demand quality-tier
reconnect without dropping peers, and routes input over each peer's
DataChannel — matching `2026-08-30-engine-full-migration-design.md`.

**Architecture:** Split the current monolithic `WebRtcPeer` (offerer) into
`ScrcpySource` (owns the scrcpy socket generation + source-global SPS/PPS
cache + health state), `PeerSession` (one answerer peer connection per
viewer), and `PeerRegistry` (thread-safe fan-out + capacity + reaping). Two
`cpp-httplib` listeners expose WHEP (external) and admin (loopback-only).
`SignalingClient`'s existing raw-text transport is reused unchanged; only
the message-handling logic above it changes, from JSON-envelope/offerer to
raw-SDP/answerer.

**Tech Stack:** C++20, libdatachannel, cpp-httplib (new), GTest,
CMake/vcpkg, Windows Host PC only (unbuilt/unverified until run there — see
`engine/BUILD_WINDOWS.md`, do not assert this plan's code builds without
checking).

**Spec:** `docs/superpowers/specs/2026-08-30-engine-full-migration-design.md`
— this plan implements that spec's "Required C++ engine design changes"
section plus the parts of "Architecture," "Negotiation and peer lifecycle,"
"Reconnect / quality-tier mechanism," "Input," and "Endpoint discovery,
authentication, and network binding" that live inside `engine.exe`. Python
orchestration (capability minting, engine spawn/supervision, `/select`
changes), client changes, CI enablement, and the final cutover gates are
out of scope — separate plans per the brainstorming decomposition.

## Global Constraints

- **Scope note on the spec's "structured lifecycle logs":** this plan
  satisfies "suitable for Python supervision" via the structured JSON
  ready record (Task 1) plus `/admin/health`'s structured JSON polling
  response (Task 6) — Python's supervision loop consumes those, not
  parsed stdout log lines. Free-text `[debug]`/`[peer]`-prefixed
  stdout/stderr output (existing style, carried forward unchanged) covers
  "packaged Windows diagnosis" for a human reading logs. A dedicated
  structured-logging framework (e.g. one JSON object per log line) is
  intentionally out of scope here — nothing in this plan's own tasks or
  Final Verification section depends on it; revisit only if a later plan's
  diagnosis needs prove free-text logs insufficient.
- `engine/CMakeLists.txt` hard-fails outside Windows. Every build/test
  command in this plan runs on the Windows Host PC or the `build-engine`
  GH Actions job — never assert success without checking one of those.
- Both WHEP and VPS-signaling negotiation are **non-trickle**: the engine
  gathers all local ICE candidates before sending its answer. No partial
  candidate is ever sent standalone.
- The viewer is always the offerer on both transports. The engine never
  calls `setLocalDescription()` to produce an offer; it only answers.
- One process serves exactly one instance. WHEP and admin listeners bind
  to OS-assigned ephemeral ports (`0`), never a fixed/CLI-supplied port.
- The admin listener binds `127.0.0.1` only, on a different port than WHEP.
  `POST /admin/reconnect` and `POST /admin/keyframe` are never reachable
  from the WHEP listener's bind address.
- Local WHEP sessions are capped at a configurable limit, default 4;
  requests beyond the cap get HTTP 503. At most one public (VPS) peer
  exists at a time; a new offer replaces the previous public peer.
- The source-global `h264::SpsPpsCache` is observed once per access unit,
  before fan-out to peers, and is reset whenever the scrcpy generation
  changes (`Reset()` is new in this plan — see Task 5).
- `engine.exe` never receives the raw shared `AUTH_TOKEN`. It validates a
  short-lived, instance-scoped bearer capability, using the same
  `<payload>.<hmac-hex>` shape as `src/server/auth.py`'s existing session
  cookie (`hmac.new(secret, payload, sha256).hexdigest()`), so Python's
  minting side (a later plan) can reuse a familiar pattern.
- No external dependencies beyond what's already in `engine/vcpkg.json`
  plus `cpp-httplib` (single-header, vcpkg-available, header-only — no new
  runtime DLL).
- Comments explain non-obvious protocol constraints, not line-by-line
  mechanics, matching existing files in `engine/src/`.

---

### Task 1: Dual HTTP listeners + structured ready record (skeleton)

**Files:**
- Create: `engine/src/http_server.h`
- Create: `engine/src/http_server.cpp`
- Create: `engine/test/test_http_server.cpp`
- Modify: `engine/CMakeLists.txt` (add `cpp-httplib` dependency, new sources)
- Modify: `engine/vcpkg.json` (add `cpp-httplib`)

**Interfaces:**
- Produces for later tasks:
  - `class EngineHttpServer` — wraps one `httplib::Server`, binds to `0.0.0.0`
    or `127.0.0.1` on port `0`, exposes the OS-assigned port via `Port()`,
    and runs its accept loop on a background thread (`Start()`/`Stop()`).
  - `std::string BuildReadyRecord(const std::string& instanceName, int pid, int whepPort, int adminPort, uint64_t generation, int width, int height)` in a new `engine/src/ready_record.h`/`.cpp` pair — returns one JSON line (no trailing newline; caller prints it followed by `std::endl`).

This task only stands up the plumbing (empty WHEP server with a
placeholder `GET /` returning 200, empty admin server with `GET
/admin/health` returning a stub JSON) so later tasks can attach real
routes without also debugging listener setup.

- [ ] **Step 1: Write the failing test**

Create `engine/test/test_http_server.cpp`:

```cpp
#include <gtest/gtest.h>
#include "http_server.h"
#include "ready_record.h"
#include <httplib.h>
#include <nlohmann/json.hpp>

using json = nlohmann::json;

TEST(EngineHttpServer, BindsToEphemeralPortAndServes) {
    EngineHttpServer server("0.0.0.0");
    server.Server().Get("/", [](const httplib::Request&, httplib::Response& res) {
        res.set_content("ok", "text/plain");
    });
    server.Start();
    ASSERT_GT(server.Port(), 0);

    httplib::Client client("127.0.0.1", server.Port());
    auto res = client.Get("/");
    ASSERT_TRUE(res);
    EXPECT_EQ(res->status, 200);
    EXPECT_EQ(res->body, "ok");

    server.Stop();
}

TEST(EngineHttpServer, LoopbackBindRefusesNonLoopbackByAddressChoice) {
    // The admin listener's security boundary is *which address it binds*,
    // not application-level filtering — this test only documents that the
    // constructor accepts an explicit bind address distinct from WHEP's.
    EngineHttpServer admin("127.0.0.1");
    admin.Start();
    ASSERT_GT(admin.Port(), 0);
    EXPECT_NE(admin.Port(), 0);
    admin.Stop();
}

TEST(ReadyRecord, ContainsAllRequiredFields) {
    std::string line = BuildReadyRecord("instance0", 4242, 51000, 51001, 3, 1080, 1920);
    auto parsed = json::parse(line, nullptr, false);
    ASSERT_FALSE(parsed.is_discarded());
    EXPECT_EQ(parsed["instance_name"], "instance0");
    EXPECT_EQ(parsed["pid"], 4242);
    EXPECT_EQ(parsed["whep_port"], 51000);
    EXPECT_EQ(parsed["admin_port"], 51001);
    EXPECT_EQ(parsed["generation"], 3);
    EXPECT_EQ(parsed["width"], 1080);
    EXPECT_EQ(parsed["height"], 1920);
}
```

- [ ] **Step 2: Add `cpp-httplib` to vcpkg and wire the new test file**

In `engine/vcpkg.json`, add `"cpp-httplib"` to the `dependencies` array
(alphabetical position, matching the existing list style):

```json
{
  "name": "window-control-engine",
  "version": "0.1.0",
  "dependencies": [
    { "name": "libdatachannel", "features": ["srtp"] },
    "cpp-httplib",
    "gtest",
    "websocketpp",
    { "name": "asio", "features": [] },
    "nlohmann-json"
  ]
}
```

In `engine/CMakeLists.txt`, add `find_package(httplib CONFIG REQUIRED)`
after the existing `find_package(nlohmann_json ...)` line, add
`httplib::httplib` to `engine_core`'s `target_link_libraries`, add
`src/http_server.cpp` and `src/ready_record.cpp` to `engine_core`'s source
list, and add `test/test_http_server.cpp` to `engine_tests`'s source list:

```cmake
find_package(nlohmann_json CONFIG REQUIRED)
find_package(httplib CONFIG REQUIRED)
find_package(OpenSSL REQUIRED)
```

```cmake
add_library(engine_core
  src/scrcpy_video.cpp
  src/scrcpy_control.cpp
  src/signaling_client.cpp
  src/peer.cpp
  src/h264_nalu.cpp
  src/http_server.cpp
  src/ready_record.cpp
)
```

```cmake
target_link_libraries(engine_core PUBLIC
  LibDataChannel::LibDataChannel
  websocketpp::websocketpp
  asio::asio
  nlohmann_json::nlohmann_json
  httplib::httplib
  OpenSSL::SSL
  OpenSSL::Crypto
  ws2_32.lib
  crypt32.lib
)
```

```cmake
add_executable(engine_tests
  test/test_scrcpy_video.cpp
  test/test_scrcpy_control.cpp
  test/test_signaling_client.cpp
  test/test_h264_nalu.cpp
  test/test_http_server.cpp
)
```

(Leave `src/peer.cpp`/`test/test_h264_nalu.cpp` as-is here — `peer.cpp` is
replaced in Task 2 and deleted from the build in Task 10, not this step.)

- [ ] **Step 3: Verify the red state**

On the Windows Host PC:

```powershell
cmake --build engine\build --config Release --target engine_tests
```

Expected: build fails — `http_server.h`/`ready_record.h` don't exist yet.
This is the deliberate red state.

- [ ] **Step 4: Implement `EngineHttpServer` and `BuildReadyRecord`**

Create `engine/src/http_server.h`:

```cpp
#pragma once
#include <httplib.h>
#include <atomic>
#include <string>
#include <thread>

// Thin wrapper around one httplib::Server bound to an OS-assigned
// ephemeral port. Callers register routes on Server() before calling
// Start() — httplib does not support adding routes after Listen() begins
// accepting connections on its own thread.
class EngineHttpServer {
public:
    explicit EngineHttpServer(std::string bindAddress);
    ~EngineHttpServer();

    EngineHttpServer(const EngineHttpServer&) = delete;
    EngineHttpServer& operator=(const EngineHttpServer&) = delete;

    httplib::Server& Server();
    void Start();
    void Stop();
    int Port() const;

private:
    std::string bindAddress_;
    httplib::Server server_;
    std::thread serveThread_;
    std::atomic<int> port_{0};
};
```

Create `engine/src/http_server.cpp`:

```cpp
#include "http_server.h"
#include <stdexcept>

EngineHttpServer::EngineHttpServer(std::string bindAddress)
    : bindAddress_(std::move(bindAddress)) {}

EngineHttpServer::~EngineHttpServer() { Stop(); }

httplib::Server& EngineHttpServer::Server() { return server_; }

void EngineHttpServer::Start() {
    // bind_to_any_port + listen_after_bind splits port selection from the
    // blocking accept loop, so Port() is valid the instant Start() returns
    // instead of racing the background thread's own bind() call.
    int bound = server_.bind_to_port(bindAddress_.c_str(), 0);
    if (bound <= 0) {
        throw std::runtime_error("EngineHttpServer: bind_to_port failed on " + bindAddress_);
    }
    port_.store(bound);
    serveThread_ = std::thread([this]() { server_.listen_after_bind(); });
}

void EngineHttpServer::Stop() {
    if (!serveThread_.joinable()) return;
    server_.stop();
    serveThread_.join();
}

int EngineHttpServer::Port() const { return port_.load(); }
```

Create `engine/src/ready_record.h`:

```cpp
#pragma once
#include <cstdint>
#include <string>

std::string BuildReadyRecord(
    const std::string& instanceName,
    int pid,
    int whepPort,
    int adminPort,
    std::uint64_t generation,
    int width,
    int height);
```

Create `engine/src/ready_record.cpp`:

```cpp
#include "ready_record.h"
#include <nlohmann/json.hpp>

using json = nlohmann::json;

std::string BuildReadyRecord(
    const std::string& instanceName,
    int pid,
    int whepPort,
    int adminPort,
    std::uint64_t generation,
    int width,
    int height) {
    json record = {
        {"instance_name", instanceName},
        {"pid", pid},
        {"whep_port", whepPort},
        {"admin_port", adminPort},
        {"generation", generation},
        {"width", width},
        {"height", height},
    };
    return record.dump();
}
```

- [ ] **Step 5: Build and run the new tests**

```powershell
cmake --build engine\build --config Release --target engine_tests
.\engine\build\Release\engine_tests.exe --gtest_filter="EngineHttpServer.*:ReadyRecord.*"
```

Expected: all three tests pass.

- [ ] **Step 6: Commit**

```bash
git add engine/src/http_server.h engine/src/http_server.cpp \
  engine/src/ready_record.h engine/src/ready_record.cpp \
  engine/test/test_http_server.cpp engine/CMakeLists.txt engine/vcpkg.json
git commit -m "feat(engine): add HTTP listener wrapper and ready record"
```

---

### Task 2: PeerSession — answerer-side WebRTC peer

**Files:**
- Create: `engine/src/peer_session.h`
- Create: `engine/src/peer_session.cpp`
- Create: `engine/test/test_peer_session.cpp`
- Modify: `engine/CMakeLists.txt` (add new sources)

**Interfaces:**
- Consumes from existing code: `h264::SpsPpsCache` is now owned by
  `ScrcpySource` (Task 5), not `PeerSession` — `PeerSession` takes prepared
  bytes as input to `SendVideoNalu`, it does not run the cache itself.
- Produces for Task 3/4/7/8:
  ```cpp
  class PeerSession {
  public:
      using InputCallback = std::function<void(const std::string& jsonMessage)>;
      using StateCallback = std::function<void(rtc::PeerConnection::State)>;

      PeerSession(std::string id, const std::vector<std::string>& iceServers);
      ~PeerSession();

      // Blocks the calling thread until ICE gathering completes (non-trickle),
      // then returns the complete local SDP answer. Throws std::runtime_error
      // on timeout (default 10s) or on a malformed/rejected remote offer.
      std::string AnswerOffer(const std::string& remoteSdpOffer,
                               std::chrono::milliseconds gatherTimeout =
                                   std::chrono::milliseconds(10000));

      void SendVideoNalu(const uint8_t* data, size_t size);
      void SetInputCallback(InputCallback onInput);
      void SetOnStateChange(StateCallback onStateChange);
      void Close();

      const std::string& Id() const;
      rtc::PeerConnection::State State() const;
  };
  ```
- `AnswerOffer` creates the send-only H264 video track and waits for the
  viewer's `"input"` DataChannel via `pc->onDataChannel` — the viewer
  creates it, matching the spec's "viewer creates it before producing
  either its WHEP or VPS offer."

- [ ] **Step 1: Write the failing test**

Create `engine/test/test_peer_session.cpp`. This test builds a real
second-side `rtc::PeerConnection` in the test process to negotiate against
`PeerSession`, entirely in-process (no network) — the same technique
libdatachannel's own examples use for loopback tests:

```cpp
#include <gtest/gtest.h>
#include "peer_session.h"
#include <rtc/rtc.hpp>
#include <atomic>
#include <chrono>
#include <thread>

namespace {

// Drives a bare rtc::PeerConnection through the *offerer* side, waiting
// for its own ICE gathering to complete before returning the offer SDP —
// mirrors what a real browser/mobile WHEP or VPS-signaling client does.
std::string CreateGatheredOffer(rtc::PeerConnection& pc) {
    pc.addTransceiver(rtc::Description::Media::Kind::Video,
                       rtc::Description::Direction::RecvOnly);
    pc.createDataChannel("input");

    std::atomic<bool> gathered{false};
    pc.onGatheringStateChange([&](rtc::PeerConnection::GatheringState state) {
        if (state == rtc::PeerConnection::GatheringState::Complete) gathered = true;
    });
    pc.setLocalDescription();

    for (int i = 0; i < 200 && !gathered; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(25));
    }
    return std::string(*pc.localDescription());
}

} // namespace

TEST(PeerSession, AnswersOfferAndReachesConnected) {
    rtc::Configuration viewerConfig;
    rtc::PeerConnection viewerPc(viewerConfig);
    std::string offer = CreateGatheredOffer(viewerPc);

    PeerSession session("test-peer-1", {});
    std::string answer = session.AnswerOffer(offer);
    EXPECT_NE(answer.find("v=0"), std::string::npos);

    viewerPc.setRemoteDescription(rtc::Description(answer, "answer"));

    std::atomic<bool> connected{false};
    viewerPc.onStateChange([&](rtc::PeerConnection::State s) {
        if (s == rtc::PeerConnection::State::Connected) connected = true;
    });

    for (int i = 0; i < 200 && !connected; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(25));
    }
    EXPECT_TRUE(connected);
    EXPECT_EQ(session.State(), rtc::PeerConnection::State::Connected);

    session.Close();
}

TEST(PeerSession, InvokesInputCallbackOnViewerDataChannelMessage) {
    rtc::Configuration viewerConfig;
    rtc::PeerConnection viewerPc(viewerConfig);
    auto inputChannel = viewerPc.createDataChannel("input");

    // addTransceiver/offer must happen after createDataChannel above so the
    // SDP includes the application m-line the DataChannel needs.
    viewerPc.addTransceiver(rtc::Description::Media::Kind::Video,
                             rtc::Description::Direction::RecvOnly);
    std::atomic<bool> gathered{false};
    viewerPc.onGatheringStateChange([&](rtc::PeerConnection::GatheringState s) {
        if (s == rtc::PeerConnection::GatheringState::Complete) gathered = true;
    });
    viewerPc.setLocalDescription();
    for (int i = 0; i < 200 && !gathered; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(25));
    }
    std::string offer(*viewerPc.localDescription());

    PeerSession session("test-peer-2", {});
    std::string received;
    std::atomic<bool> gotMessage{false};
    session.SetInputCallback([&](const std::string& msg) {
        received = msg;
        gotMessage = true;
    });

    std::string answer = session.AnswerOffer(offer);
    viewerPc.setRemoteDescription(rtc::Description(answer, "answer"));

    std::atomic<bool> dcOpen{false};
    inputChannel->onOpen([&]() { dcOpen = true; });
    for (int i = 0; i < 200 && !dcOpen; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(25));
    }
    ASSERT_TRUE(dcOpen);

    inputChannel->send(std::string(R"({"type":"click","x":0.5,"y":0.5})"));
    for (int i = 0; i < 200 && !gotMessage; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(25));
    }
    EXPECT_TRUE(gotMessage);
    EXPECT_EQ(received, R"({"type":"click","x":0.5,"y":0.5})");

    session.Close();
}
```

Add `test/test_peer_session.cpp` to `engine_tests` in `engine/CMakeLists.txt`.

- [ ] **Step 2: Verify the red state**

```powershell
cmake --build engine\build --config Release --target engine_tests
```
Expected: fails — `peer_session.h` doesn't exist.

- [ ] **Step 3: Implement `PeerSession`**

Create `engine/src/peer_session.h`:

```cpp
#pragma once
#include <rtc/rtc.hpp>
#include <chrono>
#include <functional>
#include <memory>
#include <string>
#include <vector>

class PeerSession {
public:
    using InputCallback = std::function<void(const std::string& jsonMessage)>;
    using StateCallback = std::function<void(rtc::PeerConnection::State)>;

    PeerSession(std::string id, const std::vector<std::string>& iceServers);
    ~PeerSession();

    PeerSession(const PeerSession&) = delete;
    PeerSession& operator=(const PeerSession&) = delete;

    std::string AnswerOffer(const std::string& remoteSdpOffer,
                             std::chrono::milliseconds gatherTimeout =
                                 std::chrono::milliseconds(10000));

    void SendVideoNalu(const uint8_t* data, size_t size);
    void SetInputCallback(InputCallback onInput);
    void SetOnStateChange(StateCallback onStateChange);
    void Close();

    const std::string& Id() const;
    rtc::PeerConnection::State State() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
```

Create `engine/src/peer_session.cpp`. This reuses exactly the video-track
and RTP-packetizer setup `peer.cpp`'s `StartAsOfferer` already does
(`addH264Codec(96)`, `H264RtpPacketizer` with `StartSequence`, SR reporter,
NACK responder) — the only structural change from the old code is
answering instead of offering, and accepting rather than creating the
input DataChannel:

```cpp
#include "peer_session.h"
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <stdexcept>

struct PeerSession::Impl {
    std::string id;
    std::shared_ptr<rtc::PeerConnection> pc;
    std::shared_ptr<rtc::Track> videoTrack;
    std::shared_ptr<rtc::RtpPacketizationConfig> rtpConfig;
    std::shared_ptr<rtc::H264RtpPacketizer> packetizer;
    std::shared_ptr<rtc::DataChannel> inputChannel;
    InputCallback onInput;
    StateCallback onStateChange;
    std::chrono::steady_clock::time_point streamStart;

    std::mutex gatherMutex;
    std::condition_variable gatherCv;
    bool gatheringComplete = false;
};

PeerSession::PeerSession(std::string id, const std::vector<std::string>& iceServers)
    : impl_(std::make_unique<Impl>()) {
    impl_->id = std::move(id);

    rtc::Configuration config;
    for (const auto& url : iceServers) config.iceServers.emplace_back(url);
    impl_->pc = std::make_shared<rtc::PeerConnection>(config);

    impl_->pc->onGatheringStateChange([this](rtc::PeerConnection::GatheringState state) {
        if (state != rtc::PeerConnection::GatheringState::Complete) return;
        std::lock_guard<std::mutex> lock(impl_->gatherMutex);
        impl_->gatheringComplete = true;
        impl_->gatherCv.notify_all();
    });

    impl_->pc->onStateChange([this](rtc::PeerConnection::State state) {
        if (impl_->onStateChange) impl_->onStateChange(state);
    });

    // The viewer creates "input" (see peer_session_test.cpp); this side only
    // observes it arriving.
    impl_->pc->onDataChannel([this](std::shared_ptr<rtc::DataChannel> dc) {
        if (dc->label() != "input") return;
        impl_->inputChannel = dc;
        impl_->inputChannel->onMessage([this](rtc::message_variant data) {
            if (!impl_->onInput) return;
            if (std::holds_alternative<std::string>(data)) {
                impl_->onInput(std::get<std::string>(data));
            }
        });
    });
}

PeerSession::~PeerSession() { Close(); }

std::string PeerSession::AnswerOffer(
    const std::string& remoteSdpOffer, std::chrono::milliseconds gatherTimeout) {
    impl_->streamStart = std::chrono::steady_clock::now();
    impl_->pc->setRemoteDescription(rtc::Description(remoteSdpOffer, "offer"));

    rtc::Description::Video media("video", rtc::Description::Direction::SendOnly);
    media.addH264Codec(96);
    media.setBitrate(8000);
    impl_->videoTrack = impl_->pc->addTrack(media);

    impl_->rtpConfig = std::make_shared<rtc::RtpPacketizationConfig>(
        /*ssrc=*/1, /*cname=*/"engine-video", /*payloadType=*/96,
        rtc::H264RtpPacketizer::defaultClockRate);
    impl_->packetizer = std::make_shared<rtc::H264RtpPacketizer>(
        rtc::NalUnit::Separator::StartSequence, impl_->rtpConfig);
    auto srReporter = std::make_shared<rtc::RtcpSrReporter>(impl_->rtpConfig);
    impl_->packetizer->addToChain(srReporter);
    auto nackResponder = std::make_shared<rtc::RtcpNackResponder>();
    impl_->packetizer->addToChain(nackResponder);
    impl_->videoTrack->setMediaHandler(impl_->packetizer);

    impl_->pc->setLocalDescription();

    std::unique_lock<std::mutex> lock(impl_->gatherMutex);
    bool ok = impl_->gatherCv.wait_for(lock, gatherTimeout,
        [this] { return impl_->gatheringComplete; });
    if (!ok) throw std::runtime_error("PeerSession: ICE gathering timed out");

    return std::string(*impl_->pc->localDescription());
}

void PeerSession::SendVideoNalu(const uint8_t* data, size_t size) {
    if (!impl_->videoTrack || !impl_->videoTrack->isOpen()) return;

    auto elapsed = std::chrono::steady_clock::now() - impl_->streamStart;
    double elapsedSeconds = std::chrono::duration<double>(elapsed).count();
    impl_->rtpConfig->timestamp = impl_->rtpConfig->startTimestamp +
        impl_->rtpConfig->secondsToTimestamp(elapsedSeconds);

    impl_->videoTrack->send(reinterpret_cast<const std::byte*>(data), size);
}

void PeerSession::SetInputCallback(InputCallback onInput) {
    impl_->onInput = std::move(onInput);
}

void PeerSession::SetOnStateChange(StateCallback onStateChange) {
    impl_->onStateChange = std::move(onStateChange);
}

void PeerSession::Close() {
    if (impl_ && impl_->pc) impl_->pc->close();
}

const std::string& PeerSession::Id() const { return impl_->id; }

rtc::PeerConnection::State PeerSession::State() const {
    return impl_->pc->state();
}
```

Add `src/peer_session.cpp` to `engine_core` and `test/test_peer_session.cpp`
to `engine_tests` in `engine/CMakeLists.txt`.

- [ ] **Step 4: Build and run**

```powershell
cmake --build engine\build --config Release --target engine_tests
.\engine\build\Release\engine_tests.exe --gtest_filter="PeerSession.*"
```
Expected: both tests pass. (Real ICE negotiation between two in-process
peers can take a couple seconds — this is expected, not a hang.)

- [ ] **Step 5: Commit**

```bash
git add engine/src/peer_session.h engine/src/peer_session.cpp \
  engine/test/test_peer_session.cpp engine/CMakeLists.txt
git commit -m "feat(engine): add answerer-side PeerSession"
```

---

### Task 3: PeerRegistry — multi-peer fan-out, capacity, and reaping

**Files:**
- Create: `engine/src/peer_registry.h`
- Create: `engine/src/peer_registry.cpp`
- Create: `engine/test/test_peer_registry.cpp`
- Modify: `engine/CMakeLists.txt`

**Interfaces:**
- Consumes: `PeerSession` (Task 2).
- Produces for Task 4/5/7:
  ```cpp
  enum class PeerKind { Local, Public };

  class PeerRegistry {
  public:
      explicit PeerRegistry(int localCapacity = 4,
                             std::chrono::milliseconds handshakeTimeout =
                                 std::chrono::milliseconds(15000));

      // Returns nullptr (caller returns 503) if kind==Local and the local
      // capacity is already full. A new Public peer always replaces and
      // closes any existing public peer first — at most one exists.
      std::shared_ptr<PeerSession> Create(
          PeerKind kind, const std::string& id,
          const std::vector<std::string>& iceServers);

      bool Remove(const std::string& id);
      std::shared_ptr<PeerSession> Find(const std::string& id) const;

      // Snapshot for the source's fan-out loop — never call SendVideoNalu
      // while holding the registry's internal lock (a slow/blocked peer
      // send must not stall peer add/remove for every other peer).
      std::vector<std::shared_ptr<PeerSession>> Snapshot() const;

      // Removes any peer whose State() is Failed/Closed/Disconnected, or
      // whose handshake has exceeded handshakeTimeout without reaching
      // Connected. Call periodically from a housekeeping loop.
      void ReapDeadAndStalePeers();

      size_t LocalCount() const;
      bool HasPublicPeer() const;
  };
  ```

- [ ] **Step 1: Write the failing test**

Create `engine/test/test_peer_registry.cpp`:

```cpp
#include <gtest/gtest.h>
#include "peer_registry.h"
#include <thread>
#include <chrono>

TEST(PeerRegistry, EnforcesLocalCapacityLimit) {
    PeerRegistry registry(/*localCapacity=*/2);
    auto p1 = registry.Create(PeerKind::Local, "l1", {});
    auto p2 = registry.Create(PeerKind::Local, "l2", {});
    auto p3 = registry.Create(PeerKind::Local, "l3", {});

    EXPECT_NE(p1, nullptr);
    EXPECT_NE(p2, nullptr);
    EXPECT_EQ(p3, nullptr);
    EXPECT_EQ(registry.LocalCount(), 2u);
}

TEST(PeerRegistry, NewPublicPeerReplacesPrevious) {
    PeerRegistry registry;
    auto first = registry.Create(PeerKind::Public, "pub1", {});
    ASSERT_NE(first, nullptr);
    EXPECT_TRUE(registry.HasPublicPeer());

    auto second = registry.Create(PeerKind::Public, "pub2", {});
    ASSERT_NE(second, nullptr);
    EXPECT_EQ(registry.Find("pub1"), nullptr);
    EXPECT_NE(registry.Find("pub2"), nullptr);
}

TEST(PeerRegistry, RemoveDropsPeerAndFreesCapacity) {
    PeerRegistry registry(/*localCapacity=*/1);
    auto p1 = registry.Create(PeerKind::Local, "l1", {});
    ASSERT_NE(p1, nullptr);
    EXPECT_TRUE(registry.Remove("l1"));
    EXPECT_EQ(registry.LocalCount(), 0u);

    auto p2 = registry.Create(PeerKind::Local, "l2", {});
    EXPECT_NE(p2, nullptr);
}

TEST(PeerRegistry, ReapDeadAndStalePeersRemovesTimedOutHandshake) {
    PeerRegistry registry(/*localCapacity=*/4, /*handshakeTimeout=*/std::chrono::milliseconds(50));
    auto p1 = registry.Create(PeerKind::Local, "l1", {});
    ASSERT_NE(p1, nullptr);

    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    registry.ReapDeadAndStalePeers();

    EXPECT_EQ(registry.Find("l1"), nullptr);
    EXPECT_EQ(registry.LocalCount(), 0u);
}

TEST(PeerRegistry, SnapshotReflectsCurrentPeers) {
    PeerRegistry registry;
    registry.Create(PeerKind::Local, "l1", {});
    registry.Create(PeerKind::Public, "pub1", {});

    auto snap = registry.Snapshot();
    EXPECT_EQ(snap.size(), 2u);
}
```

Add `test/test_peer_registry.cpp` to `engine_tests`.

- [ ] **Step 2: Verify red state, then implement**

```powershell
cmake --build engine\build --config Release --target engine_tests
```
Expected: fails — `peer_registry.h` doesn't exist.

Create `engine/src/peer_registry.h`:

```cpp
#pragma once
#include "peer_session.h"
#include <chrono>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

enum class PeerKind { Local, Public };

class PeerRegistry {
public:
    explicit PeerRegistry(int localCapacity = 4,
                           std::chrono::milliseconds handshakeTimeout =
                               std::chrono::milliseconds(15000));

    std::shared_ptr<PeerSession> Create(
        PeerKind kind, const std::string& id,
        const std::vector<std::string>& iceServers);

    bool Remove(const std::string& id);
    std::shared_ptr<PeerSession> Find(const std::string& id) const;
    std::vector<std::shared_ptr<PeerSession>> Snapshot() const;
    void ReapDeadAndStalePeers();

    size_t LocalCount() const;
    bool HasPublicPeer() const;

private:
    struct Entry {
        std::shared_ptr<PeerSession> session;
        PeerKind kind;
        std::chrono::steady_clock::time_point createdAt;
    };

    mutable std::mutex mutex_;
    std::map<std::string, Entry> peers_;
    int localCapacity_;
    std::chrono::milliseconds handshakeTimeout_;
};
```

Create `engine/src/peer_registry.cpp`:

```cpp
#include "peer_registry.h"

PeerRegistry::PeerRegistry(int localCapacity, std::chrono::milliseconds handshakeTimeout)
    : localCapacity_(localCapacity), handshakeTimeout_(handshakeTimeout) {}

std::shared_ptr<PeerSession> PeerRegistry::Create(
    PeerKind kind, const std::string& id, const std::vector<std::string>& iceServers) {
    std::lock_guard<std::mutex> lock(mutex_);

    if (kind == PeerKind::Local) {
        size_t localCount = 0;
        for (const auto& [_, entry] : peers_) {
            if (entry.kind == PeerKind::Local) ++localCount;
        }
        if (localCount >= static_cast<size_t>(localCapacity_)) return nullptr;
    } else {
        // At most one public peer: close and drop the previous one first so
        // a stale peer never lingers holding scrcpy fan-out bandwidth.
        for (auto it = peers_.begin(); it != peers_.end();) {
            if (it->second.kind == PeerKind::Public) {
                it->second.session->Close();
                it = peers_.erase(it);
            } else {
                ++it;
            }
        }
    }

    auto session = std::make_shared<PeerSession>(id, iceServers);
    peers_[id] = Entry{session, kind, std::chrono::steady_clock::now()};
    return session;
}

bool PeerRegistry::Remove(const std::string& id) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = peers_.find(id);
    if (it == peers_.end()) return false;
    it->second.session->Close();
    peers_.erase(it);
    return true;
}

std::shared_ptr<PeerSession> PeerRegistry::Find(const std::string& id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = peers_.find(id);
    return it != peers_.end() ? it->second.session : nullptr;
}

std::vector<std::shared_ptr<PeerSession>> PeerRegistry::Snapshot() const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<std::shared_ptr<PeerSession>> result;
    result.reserve(peers_.size());
    for (const auto& [_, entry] : peers_) result.push_back(entry.session);
    return result;
}

void PeerRegistry::ReapDeadAndStalePeers() {
    std::lock_guard<std::mutex> lock(mutex_);
    auto now = std::chrono::steady_clock::now();
    for (auto it = peers_.begin(); it != peers_.end();) {
        auto state = it->second.session->State();
        bool dead = state == rtc::PeerConnection::State::Failed ||
                    state == rtc::PeerConnection::State::Closed ||
                    state == rtc::PeerConnection::State::Disconnected;
        bool staleHandshake = state != rtc::PeerConnection::State::Connected &&
                              (now - it->second.createdAt) > handshakeTimeout_;
        if (dead || staleHandshake) {
            it->second.session->Close();
            it = peers_.erase(it);
        } else {
            ++it;
        }
    }
}

size_t PeerRegistry::LocalCount() const {
    std::lock_guard<std::mutex> lock(mutex_);
    size_t count = 0;
    for (const auto& [_, entry] : peers_) {
        if (entry.kind == PeerKind::Local) ++count;
    }
    return count;
}

bool PeerRegistry::HasPublicPeer() const {
    std::lock_guard<std::mutex> lock(mutex_);
    for (const auto& [_, entry] : peers_) {
        if (entry.kind == PeerKind::Public) return true;
    }
    return false;
}
```

Add `src/peer_registry.cpp` to `engine_core` in `engine/CMakeLists.txt`.

- [ ] **Step 3: Build and run**

```powershell
cmake --build engine\build --config Release --target engine_tests
.\engine\build\Release\engine_tests.exe --gtest_filter="PeerRegistry.*"
```
Expected: all 5 tests pass.

- [ ] **Step 4: Commit**

```bash
git add engine/src/peer_registry.h engine/src/peer_registry.cpp \
  engine/test/test_peer_registry.cpp engine/CMakeLists.txt
git commit -m "feat(engine): add multi-peer registry with capacity and reaping"
```

---

### Task 4: WHEP capability auth + WHEP POST/DELETE endpoints

**Files:**
- Create: `engine/src/whep_capability.h`
- Create: `engine/src/whep_capability.cpp`
- Create: `engine/src/whep_handler.h`
- Create: `engine/src/whep_handler.cpp`
- Create: `engine/test/test_whep_capability.cpp`
- Create: `engine/test/test_whep_handler.cpp`
- Modify: `engine/CMakeLists.txt`

**Interfaces:**
- Consumes: `PeerRegistry`/`PeerSession` (Tasks 2/3), `EngineHttpServer`
  (Task 1).
- Produces for Task 9 (main.cpp wiring):
  ```cpp
  // Capability token shape: "<expiry_unix>.<instance_name>.<hmac_hex>",
  // hmac_hex = hex(hmac_sha256(secret, "<expiry_unix>.<instance_name>")) —
  // deliberately mirrors src/server/auth.py's session-cookie shape so the
  // Python minting side (a later plan) can reuse the same pattern.
  struct WhepCapabilityConfig {
      std::string secret;       // empty => auth disabled, every request allowed
      std::string instanceName;
  };

  bool ValidateWhepCapability(const WhepCapabilityConfig& config,
                              const std::string& bearerToken,
                              std::chrono::system_clock::time_point now =
                                  std::chrono::system_clock::now());

  class WhepHandler {
  public:
      WhepHandler(PeerRegistry& registry, WhepCapabilityConfig authConfig,
                  std::vector<std::string> iceServers);
      void RegisterRoutes(httplib::Server& server);
  };
  ```
- `RegisterRoutes` installs `POST /whep`, `DELETE /whep/:session_id`, and
  `OPTIONS /whep` (CORS preflight) on the given server.

- [ ] **Step 1: Write the failing tests**

Create `engine/test/test_whep_capability.cpp`:

```cpp
#include <gtest/gtest.h>
#include "whep_capability.h"

TEST(WhepCapability, DisabledWhenSecretEmpty) {
    WhepCapabilityConfig config{"", "instance0"};
    EXPECT_TRUE(ValidateWhepCapability(config, "anything-or-nothing"));
    EXPECT_TRUE(ValidateWhepCapability(config, ""));
}

TEST(WhepCapability, RejectsMissingOrMalformedToken) {
    WhepCapabilityConfig config{"secret", "instance0"};
    EXPECT_FALSE(ValidateWhepCapability(config, ""));
    EXPECT_FALSE(ValidateWhepCapability(config, "not-enough-parts"));
    EXPECT_FALSE(ValidateWhepCapability(config, "123.instance0")); // missing hmac
}

TEST(WhepCapability, RejectsWrongInstanceOrTamperedSignature) {
    WhepCapabilityConfig config{"secret", "instance0"};
    auto now = std::chrono::system_clock::now();
    auto validExpiry = std::chrono::duration_cast<std::chrono::seconds>(
        (now + std::chrono::minutes(5)).time_since_epoch()).count();

    // Build a token the same way the (future) Python minter will, using
    // the same HMAC construction under test — see Step 3's helper.
    std::string payload = std::to_string(validExpiry) + ".instance0";
    std::string forgedToken = payload + ".deadbeef";
    EXPECT_FALSE(ValidateWhepCapability(config, forgedToken, now));

    std::string wrongInstancePayload = std::to_string(validExpiry) + ".instance1";
    EXPECT_FALSE(ValidateWhepCapability(config, wrongInstancePayload + ".deadbeef", now));
}

TEST(WhepCapability, AcceptsValidUnexpiredTokenAndRejectsExpired) {
    WhepCapabilityConfig config{"secret", "instance0"};
    auto now = std::chrono::system_clock::now();

    // Round-trip through the same construction the implementation uses —
    // BuildTestToken is a small test-only helper declared in
    // whep_capability.h guarded by a test-only namespace, mirroring how
    // production Python will mint tokens with the same shared secret.
    auto futureExpiry = std::chrono::duration_cast<std::chrono::seconds>(
        (now + std::chrono::minutes(5)).time_since_epoch()).count();
    std::string validToken = test::BuildTestCapabilityToken(config.secret, config.instanceName, futureExpiry);
    EXPECT_TRUE(ValidateWhepCapability(config, validToken, now));

    auto pastExpiry = std::chrono::duration_cast<std::chrono::seconds>(
        (now - std::chrono::minutes(5)).time_since_epoch()).count();
    std::string expiredToken = test::BuildTestCapabilityToken(config.secret, config.instanceName, pastExpiry);
    EXPECT_FALSE(ValidateWhepCapability(config, expiredToken, now));
}
```

Create `engine/test/test_whep_handler.cpp`:

```cpp
#include <gtest/gtest.h>
#include "whep_handler.h"
#include "peer_registry.h"
#include "http_server.h"
#include "whep_capability.h"
#include <httplib.h>
#include <rtc/rtc.hpp>
#include <atomic>
#include <chrono>
#include <thread>

namespace {
std::string GatheredOffer() {
    rtc::PeerConnection pc;
    pc.addTransceiver(rtc::Description::Media::Kind::Video,
                       rtc::Description::Direction::RecvOnly);
    pc.createDataChannel("input");
    std::atomic<bool> gathered{false};
    pc.onGatheringStateChange([&](rtc::PeerConnection::GatheringState s) {
        if (s == rtc::PeerConnection::GatheringState::Complete) gathered = true;
    });
    pc.setLocalDescription();
    for (int i = 0; i < 200 && !gathered; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(25));
    return std::string(*pc.localDescription());
}
}

TEST(WhepHandler, PostWithoutAuthWhenDisabledSucceedsAndReturnsLocation) {
    PeerRegistry registry;
    WhepCapabilityConfig authConfig{"", "instance0"}; // disabled
    WhepHandler handler(registry, authConfig, {});

    EngineHttpServer server("127.0.0.1");
    handler.RegisterRoutes(server.Server());
    server.Start();

    httplib::Client client("127.0.0.1", server.Port());
    auto res = client.Post("/whep", GatheredOffer(), "application/sdp");
    ASSERT_TRUE(res);
    EXPECT_EQ(res->status, 201);
    EXPECT_NE(res->get_header_value("Location"), "");
    EXPECT_NE(res->body.find("v=0"), std::string::npos);

    server.Stop();
}

TEST(WhepHandler, PostWithoutBearerRejectedWhenAuthEnabled) {
    PeerRegistry registry;
    WhepCapabilityConfig authConfig{"secret", "instance0"};
    WhepHandler handler(registry, authConfig, {});

    EngineHttpServer server("127.0.0.1");
    handler.RegisterRoutes(server.Server());
    server.Start();

    httplib::Client client("127.0.0.1", server.Port());
    auto res = client.Post("/whep", GatheredOffer(), "application/sdp");
    ASSERT_TRUE(res);
    EXPECT_EQ(res->status, 401);

    server.Stop();
}

TEST(WhepHandler, DeleteRemovesSessionFromRegistry) {
    PeerRegistry registry;
    WhepCapabilityConfig authConfig{"", "instance0"};
    WhepHandler handler(registry, authConfig, {});

    EngineHttpServer server("127.0.0.1");
    handler.RegisterRoutes(server.Server());
    server.Start();

    httplib::Client client("127.0.0.1", server.Port());
    auto postRes = client.Post("/whep", GatheredOffer(), "application/sdp");
    ASSERT_TRUE(postRes);
    std::string location = postRes->get_header_value("Location");
    ASSERT_NE(location, "");

    EXPECT_EQ(registry.LocalCount(), 1u);
    auto delRes = client.Delete(location);
    ASSERT_TRUE(delRes);
    EXPECT_EQ(delRes->status, 204);
    EXPECT_EQ(registry.LocalCount(), 0u);

    server.Stop();
}

TEST(WhepHandler, RejectsBeyondLocalCapacityWith503) {
    PeerRegistry registry(/*localCapacity=*/1);
    WhepCapabilityConfig authConfig{"", "instance0"};
    WhepHandler handler(registry, authConfig, {});

    EngineHttpServer server("127.0.0.1");
    handler.RegisterRoutes(server.Server());
    server.Start();

    httplib::Client client("127.0.0.1", server.Port());
    auto first = client.Post("/whep", GatheredOffer(), "application/sdp");
    ASSERT_TRUE(first);
    EXPECT_EQ(first->status, 201);

    auto second = client.Post("/whep", GatheredOffer(), "application/sdp");
    ASSERT_TRUE(second);
    EXPECT_EQ(second->status, 503);

    server.Stop();
}
```

Add both new test files to `engine_tests` in `engine/CMakeLists.txt`.

- [ ] **Step 2: Verify red state**

```powershell
cmake --build engine\build --config Release --target engine_tests
```
Expected: fails — `whep_capability.h`/`whep_handler.h` don't exist.

- [ ] **Step 3: Implement capability validation**

Create `engine/src/whep_capability.h`:

```cpp
#pragma once
#include <chrono>
#include <cstdint>
#include <string>

struct WhepCapabilityConfig {
    std::string secret;       // empty => auth disabled
    std::string instanceName;
};

bool ValidateWhepCapability(
    const WhepCapabilityConfig& config,
    const std::string& bearerToken,
    std::chrono::system_clock::time_point now = std::chrono::system_clock::now());

// Test-only: builds a token the same way a real minter (Python, in a later
// plan) will, using the same HMAC construction ValidateWhepCapability
// verifies against. Declared here (not in a separate test-utils target) so
// both engine_tests and any future Python-interop fixture reference one
// canonical implementation of the wire format.
namespace test {
std::string BuildTestCapabilityToken(
    const std::string& secret, const std::string& instanceName, std::int64_t expiryUnix);
}
```

Create `engine/src/whep_capability.cpp`:

```cpp
#include "whep_capability.h"
#include <openssl/evp.h>
#include <openssl/hmac.h>
#include <array>
#include <cstdio>
#include <sstream>

namespace {

std::string HexHmacSha256(const std::string& secret, const std::string& payload) {
    unsigned char digest[EVP_MAX_MD_SIZE];
    unsigned int digestLen = 0;
    HMAC(EVP_sha256(),
         secret.data(), static_cast<int>(secret.size()),
         reinterpret_cast<const unsigned char*>(payload.data()), payload.size(),
         digest, &digestLen);

    std::ostringstream out;
    for (unsigned int i = 0; i < digestLen; ++i) {
        char buf[3];
        std::snprintf(buf, sizeof(buf), "%02x", digest[i]);
        out << buf;
    }
    return out.str();
}

bool ConstantTimeEquals(const std::string& a, const std::string& b) {
    if (a.size() != b.size()) return false;
    unsigned char diff = 0;
    for (size_t i = 0; i < a.size(); ++i) diff |= static_cast<unsigned char>(a[i] ^ b[i]);
    return diff == 0;
}

} // namespace

bool ValidateWhepCapability(
    const WhepCapabilityConfig& config, const std::string& bearerToken,
    std::chrono::system_clock::time_point now) {
    if (config.secret.empty()) return true;
    if (bearerToken.empty()) return false;

    size_t firstDot = bearerToken.find('.');
    size_t lastDot = bearerToken.rfind('.');
    if (firstDot == std::string::npos || lastDot == firstDot) return false;

    std::string expiryStr = bearerToken.substr(0, firstDot);
    std::string instance = bearerToken.substr(firstDot + 1, lastDot - firstDot - 1);
    std::string signature = bearerToken.substr(lastDot + 1);
    if (expiryStr.empty() || instance.empty() || signature.empty()) return false;
    if (instance != config.instanceName) return false;

    std::int64_t expiry;
    try {
        expiry = std::stoll(expiryStr);
    } catch (const std::exception&) {
        return false;
    }

    std::string payload = expiryStr + "." + instance;
    std::string expectedSignature = HexHmacSha256(config.secret, payload);
    if (!ConstantTimeEquals(signature, expectedSignature)) return false;

    auto nowUnix = std::chrono::duration_cast<std::chrono::seconds>(
        now.time_since_epoch()).count();
    return nowUnix <= expiry;
}

namespace test {
std::string BuildTestCapabilityToken(
    const std::string& secret, const std::string& instanceName, std::int64_t expiryUnix) {
    std::string payload = std::to_string(expiryUnix) + "." + instanceName;
    return payload + "." + HexHmacSha256(secret, payload);
}
}
```

- [ ] **Step 4: Implement WHEP HTTP handlers**

Create `engine/src/whep_handler.h`:

```cpp
#pragma once
#include "peer_registry.h"
#include "whep_capability.h"
#include <httplib.h>
#include <string>
#include <vector>

class WhepHandler {
public:
    WhepHandler(PeerRegistry& registry, WhepCapabilityConfig authConfig,
                std::vector<std::string> iceServers);

    void RegisterRoutes(httplib::Server& server);

private:
    PeerRegistry& registry_;
    WhepCapabilityConfig authConfig_;
    std::vector<std::string> iceServers_;
    int nextSessionSeq_ = 0;
};
```

Create `engine/src/whep_handler.cpp`. Session ids are generated, not
client-supplied — this is the "unguessable resource identifier" the spec
requires for DELETE capability:

```cpp
#include "whep_handler.h"
#include <chrono>
#include <random>
#include <sstream>

namespace {

std::string ExtractBearerToken(const httplib::Request& req) {
    auto it = req.headers.find("Authorization");
    if (it == req.headers.end()) return "";
    const std::string prefix = "Bearer ";
    if (it->second.rfind(prefix, 0) != 0) return "";
    return it->second.substr(prefix.size());
}

std::string GenerateUnguessableId() {
    std::random_device rd;
    std::mt19937_64 gen(rd());
    std::uniform_int_distribution<std::uint64_t> dist;
    std::ostringstream out;
    out << std::hex << dist(gen) << dist(gen);
    return out.str();
}

void ApplyCorsHeaders(httplib::Response& res) {
    res.set_header("Access-Control-Allow-Origin", "*");
    res.set_header("Access-Control-Allow-Headers", "Authorization, Content-Type");
    res.set_header("Access-Control-Expose-Headers", "Location");
}

} // namespace

WhepHandler::WhepHandler(PeerRegistry& registry, WhepCapabilityConfig authConfig,
                          std::vector<std::string> iceServers)
    : registry_(registry), authConfig_(std::move(authConfig)),
      iceServers_(std::move(iceServers)) {}

void WhepHandler::RegisterRoutes(httplib::Server& server) {
    server.Options("/whep", [](const httplib::Request&, httplib::Response& res) {
        ApplyCorsHeaders(res);
        res.status = 204;
    });

    server.Post("/whep", [this](const httplib::Request& req, httplib::Response& res) {
        ApplyCorsHeaders(res);
        if (!ValidateWhepCapability(authConfig_, ExtractBearerToken(req))) {
            res.status = 401;
            return;
        }

        std::string id = "local-" + GenerateUnguessableId();
        auto session = registry_.Create(PeerKind::Local, id, iceServers_);
        if (!session) {
            res.status = 503;
            res.set_content("local session capacity reached", "text/plain");
            return;
        }

        try {
            std::string answer = session->AnswerOffer(req.body);
            res.status = 201;
            res.set_header("Location", "/whep/" + id);
            res.set_content(answer, "application/sdp");
        } catch (const std::exception& e) {
            registry_.Remove(id);
            res.status = 500;
            res.set_content(e.what(), "text/plain");
        }
    });

    server.Delete(R"(/whep/([A-Za-z0-9\-]+))", [this](const httplib::Request& req, httplib::Response& res) {
        ApplyCorsHeaders(res);
        std::string id = req.matches[1];
        res.status = registry_.Remove(id) ? 204 : 404;
    });
}
```

Add `src/whep_capability.cpp` and `src/whep_handler.cpp` to `engine_core`
in `engine/CMakeLists.txt`.

- [ ] **Step 5: Build and run**

```powershell
cmake --build engine\build --config Release --target engine_tests
.\engine\build\Release\engine_tests.exe --gtest_filter="WhepCapability.*:WhepHandler.*"
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add engine/src/whep_capability.h engine/src/whep_capability.cpp \
  engine/src/whep_handler.h engine/src/whep_handler.cpp \
  engine/test/test_whep_capability.cpp engine/test/test_whep_handler.cpp \
  engine/CMakeLists.txt
git commit -m "feat(engine): add WHEP capability auth and HTTP POST/DELETE handlers"
```

---

### Task 5: ScrcpySource — generation-based reconnect and health state

**Files:**
- Modify: `engine/src/h264_nalu.h`, `engine/src/h264_nalu.cpp` (add `Reset()`)
- Modify: `engine/test/test_h264_nalu.cpp` (add `Reset()` coverage)
- Create: `engine/src/scrcpy_source.h`
- Create: `engine/src/scrcpy_source.cpp`
- Create: `engine/test/test_scrcpy_source.cpp`
- Modify: `engine/CMakeLists.txt`

**Interfaces:**
- Consumes: `ScrcpyVideoClient`/`ScrcpyControlClient` (existing, unchanged
  public API), `h264::SpsPpsCache` (existing, plus new `Reset()`),
  `PeerRegistry::Snapshot()` (Task 3).
- Produces for Task 6/9:
  ```cpp
  enum class SourceHealthState { Connected, Disconnected, Stalled };

  struct SourceStatus {
      SourceHealthState state;
      std::uint64_t generation;
      int width;
      int height;
  };

  class ScrcpySource {
  public:
      ScrcpySource(PeerRegistry& registry,
                   std::chrono::milliseconds stallThreshold =
                       std::chrono::milliseconds(5000));
      ~ScrcpySource();

      // Blocking initial connect for generation 0 — bounded retry waiting
      // for scrcpy-server to accept connections (Python may still be
      // launching it). Throws on exhausted retries.
      void ConnectInitial(int port,
                           int maxRetries = 20,
                           std::chrono::milliseconds retryDelay =
                               std::chrono::milliseconds(250));

      // Rejects (returns false, no state change) if requestedGeneration is
      // not strictly greater than the current generation. Otherwise stops
      // the old source client, connects the new one (same bounded retry as
      // ConnectInitial), resets the SPS/PPS cache, and resumes fan-out.
      bool Reconnect(int newPort, std::uint64_t requestedGeneration);

      void RequestIdr();
      SourceStatus Status() const;
      std::shared_ptr<ScrcpyControlClient> Control() const;
  };
  ```
- `ScrcpySource` owns the fan-out: its internal `NaluCallback` runs
  `SpsPpsCache::ObserveAndPrepare` once, then calls `SendVideoNalu` on
  every peer in `registry.Snapshot()` — this is the "one source-global
  cache observed before fan-out" constraint.

- [ ] **Step 1: Add `SpsPpsCache::Reset()` (small, TDD first)**

Append to `engine/test/test_h264_nalu.cpp`:

```cpp
TEST(SpsPpsCache, ResetClearsStoredConfig) {
    SpsPpsCache cache;
    auto sps = Nalu4(0x67, {0x42, 0xC0, 0x29});
    auto pps = Nalu4(0x68, {0xCE, 0x3C, 0x80});
    cache.ObserveAndPrepare(sps.data(), sps.size());
    cache.ObserveAndPrepare(pps.data(), pps.size());
    ASSERT_TRUE(cache.HasConfig());

    cache.Reset();
    EXPECT_FALSE(cache.HasConfig());

    auto idr = Nalu4(0x65, {0xAA, 0xBB});
    EXPECT_FALSE(cache.ObserveAndPrepare(idr.data(), idr.size()).has_value());
}
```

Run `engine_tests --gtest_filter=SpsPpsCache.ResetClearsStoredConfig` on
Windows: expected FAIL (`Reset` not declared).

Add to `engine/src/h264_nalu.h`'s `SpsPpsCache` class: `void Reset();`
public method. Implement in `engine/src/h264_nalu.cpp`:

```cpp
void SpsPpsCache::Reset() {
    sps_.clear();
    pps_.clear();
}
```

Re-run the same filter: expected PASS. This sub-step has its own
commit-worthy unit but is small enough to fold into this task's overall
commit at Step 6 rather than a standalone commit — the rest of this task
depends on it immediately.

- [ ] **Step 2: Write the failing ScrcpySource tests**

Create `engine/test/test_scrcpy_source.cpp`. These tests need a real
listening TCP socket standing in for scrcpy-server, so they build a
minimal fake server inline (accept, send dummy byte + 64-byte name +
12-byte meta, then optionally close to simulate a dead source):

```cpp
#include <gtest/gtest.h>
#include "scrcpy_source.h"
#include "peer_registry.h"
#include <winsock2.h>
#include <ws2tcpip.h>
#include <atomic>
#include <chrono>
#include <thread>
#include <vector>

namespace {

// Minimal fake scrcpy-server: accepts exactly one video connection and one
// control connection (in that order, matching the real handshake's
// connect-order dependency), sends the handshake bytes, then optionally
// stays open sending nothing further (simulating a live-but-idle source)
// or closes immediately (simulating a dead source).
class FakeScrcpyServer {
public:
    FakeScrcpyServer() {
        WSADATA wsaData;
        WSAStartup(MAKEWORD(2, 2), &wsaData);
        listenSock_ = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        addr.sin_port = 0;
        bind(listenSock_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr));
        int len = sizeof(addr);
        getsockname(listenSock_, reinterpret_cast<sockaddr*>(&addr), &len);
        port_ = ntohs(addr.sin_port);
        listen(listenSock_, 2);
    }

    ~FakeScrcpyServer() {
        Stop();
        closesocket(listenSock_);
        WSACleanup();
    }

    int Port() const { return port_; }

    void Serve() {
        thread_ = std::thread([this]() {
            SOCKET videoConn = accept(listenSock_, nullptr, nullptr);
            if (videoConn == INVALID_SOCKET) return;
            char dummy = 0;
            send(videoConn, &dummy, 1, 0);

            SOCKET controlConn = accept(listenSock_, nullptr, nullptr);
            if (controlConn != INVALID_SOCKET) videoConn_ = controlConn; // keep alive, unused

            std::vector<char> name(64, 0);
            const char* deviceName = "fake-device";
            std::copy(deviceName, deviceName + strlen(deviceName), name.begin());
            send(videoConn, name.data(), 64, 0);

            unsigned char meta[12] = {0};
            // width=100 (offset 4), height=200 (offset 8), big-endian.
            meta[4] = 0; meta[5] = 0; meta[6] = 0; meta[7] = 100;
            meta[8] = 0; meta[9] = 0; meta[10] = 0; meta[11] = 200;
            send(videoConn, reinterpret_cast<char*>(meta), 12, 0);

            videoAccepted_ = true;
            // Idle after handshake — no frames — matching "connected but
            // no frames yet" until a test explicitly closes the socket.
            while (running_.load()) std::this_thread::sleep_for(std::chrono::milliseconds(20));
            closesocket(videoConn);
        });
    }

    void Stop() {
        running_ = false;
        if (thread_.joinable()) thread_.join();
    }

    bool VideoAccepted() const { return videoAccepted_.load(); }

private:
    SOCKET listenSock_ = INVALID_SOCKET;
    SOCKET videoConn_ = INVALID_SOCKET;
    int port_ = 0;
    std::thread thread_;
    std::atomic<bool> running_{true};
    std::atomic<bool> videoAccepted_{false};
};

} // namespace

TEST(ScrcpySource, ConnectInitialSucceedsAndReportsDimensions) {
    FakeScrcpyServer fake;
    fake.Serve();

    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());

    auto status = source.Status();
    EXPECT_EQ(status.width, 100);
    EXPECT_EQ(status.height, 200);
    EXPECT_EQ(status.generation, 0u);
    EXPECT_EQ(status.state, SourceHealthState::Connected);

    fake.Stop();
}

TEST(ScrcpySource, RejectsStaleReconnectGeneration) {
    FakeScrcpyServer fake1;
    fake1.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake1.Port());

    FakeScrcpyServer fake2;
    fake2.Serve();
    // generation 0 is not strictly greater than current generation 0.
    EXPECT_FALSE(source.Reconnect(fake2.Port(), 0));
    EXPECT_EQ(source.Status().generation, 0u);

    fake1.Stop();
    fake2.Stop();
}

TEST(ScrcpySource, ReconnectAdvancesGenerationAndResetsDimensions) {
    FakeScrcpyServer fake1;
    fake1.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake1.Port());

    FakeScrcpyServer fake2;
    fake2.Serve();
    EXPECT_TRUE(source.Reconnect(fake2.Port(), 1));

    auto status = source.Status();
    EXPECT_EQ(status.generation, 1u);
    EXPECT_EQ(status.width, 100); // fake server always reports 100x200
    EXPECT_EQ(status.state, SourceHealthState::Connected);

    fake1.Stop();
    fake2.Stop();
}

TEST(ScrcpySource, ReportsStalledAfterInactivityThreshold) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry, /*stallThreshold=*/std::chrono::milliseconds(100));
    source.ConnectInitial(fake.Port());

    std::this_thread::sleep_for(std::chrono::milliseconds(250));
    EXPECT_EQ(source.Status().state, SourceHealthState::Stalled);

    fake.Stop();
}
```

Add `test/test_scrcpy_source.cpp` to `engine_tests`.

- [ ] **Step 3: Verify red state, then implement `ScrcpySource`**

```powershell
cmake --build engine\build --config Release --target engine_tests
```
Expected: fails — `scrcpy_source.h` doesn't exist.

Create `engine/src/scrcpy_source.h`:

```cpp
#pragma once
#include "h264_nalu.h"
#include "peer_registry.h"
#include "scrcpy_control.h"
#include "scrcpy_video.h"
#include <atomic>
#include <chrono>
#include <cstdint>
#include <memory>
#include <mutex>

enum class SourceHealthState { Connected, Disconnected, Stalled };

struct SourceStatus {
    SourceHealthState state;
    std::uint64_t generation;
    int width;
    int height;
};

class ScrcpySource {
public:
    explicit ScrcpySource(PeerRegistry& registry,
                           std::chrono::milliseconds stallThreshold =
                               std::chrono::milliseconds(5000));
    ~ScrcpySource();

    ScrcpySource(const ScrcpySource&) = delete;
    ScrcpySource& operator=(const ScrcpySource&) = delete;

    void ConnectInitial(int port, int maxRetries = 20,
                         std::chrono::milliseconds retryDelay =
                             std::chrono::milliseconds(250));
    bool Reconnect(int newPort, std::uint64_t requestedGeneration);
    void RequestIdr();
    SourceStatus Status() const;
    std::shared_ptr<ScrcpyControlClient> Control() const;

private:
    void ConnectGenerationLocked(int port, int maxRetries,
                                  std::chrono::milliseconds retryDelay);

    PeerRegistry& registry_;
    std::chrono::milliseconds stallThreshold_;
    h264::SpsPpsCache spsPpsCache_;

    mutable std::mutex mutex_;
    std::unique_ptr<ScrcpyVideoClient> video_;
    std::shared_ptr<ScrcpyControlClient> control_;
    std::uint64_t generation_ = 0;
    int width_ = 0;
    int height_ = 0;
    std::atomic<std::chrono::steady_clock::time_point> lastFrameAt_;
    std::atomic<bool> everConnected_{false};
};
```

Create `engine/src/scrcpy_source.cpp`:

```cpp
#include "scrcpy_source.h"
#include <stdexcept>
#include <thread>

ScrcpySource::ScrcpySource(PeerRegistry& registry, std::chrono::milliseconds stallThreshold)
    : registry_(registry), stallThreshold_(stallThreshold),
      lastFrameAt_(std::chrono::steady_clock::now()) {}

ScrcpySource::~ScrcpySource() {
    std::lock_guard<std::mutex> lock(mutex_);
    if (video_) video_->Stop();
}

void ScrcpySource::ConnectGenerationLocked(
    int port, int maxRetries, std::chrono::milliseconds retryDelay) {
    // scrcpy-server may still be starting up (Python just launched it) —
    // retry the connect step itself rather than failing on the first miss.
    // Video-then-control connect order matches the documented handshake:
    // scrcpy-server's control accept() must happen before it emits the
    // video handshake bytes.
    auto video = std::make_unique<ScrcpyVideoClient>(port);
    std::exception_ptr lastError;
    bool connected = false;
    for (int attempt = 0; attempt < maxRetries; ++attempt) {
        try {
            video->Connect();
            connected = true;
            break;
        } catch (const std::exception&) {
            lastError = std::current_exception();
            std::this_thread::sleep_for(retryDelay);
        }
    }
    if (!connected) std::rethrow_exception(lastError);

    auto control = std::make_shared<ScrcpyControlClient>(port);
    control->Connect();

    video->ReadHandshake();

    video_ = std::move(video);
    control_ = std::move(control);
    width_ = video_->Width();
    height_ = video_->Height();
    spsPpsCache_.Reset();
    lastFrameAt_.store(std::chrono::steady_clock::now());
    everConnected_.store(true);

    video_->StartReading([this](const uint8_t* data, size_t size) {
        lastFrameAt_.store(std::chrono::steady_clock::now());
        auto prepared = spsPpsCache_.ObserveAndPrepare(data, size);
        const uint8_t* sendData = data;
        size_t sendSize = size;
        if (prepared.has_value()) {
            sendData = prepared->data();
            sendSize = prepared->size();
        }
        for (auto& peer : registry_.Snapshot()) {
            peer->SendVideoNalu(sendData, sendSize);
        }
    });
}

void ScrcpySource::ConnectInitial(int port, int maxRetries, std::chrono::milliseconds retryDelay) {
    std::lock_guard<std::mutex> lock(mutex_);
    ConnectGenerationLocked(port, maxRetries, retryDelay);
}

bool ScrcpySource::Reconnect(int newPort, std::uint64_t requestedGeneration) {
    std::lock_guard<std::mutex> lock(mutex_);
    if (requestedGeneration <= generation_) return false;

    if (video_) video_->Stop(); // joins the old read thread before swapping
    ConnectGenerationLocked(newPort, /*maxRetries=*/20, std::chrono::milliseconds(250));
    generation_ = requestedGeneration;
    return true;
}

void ScrcpySource::RequestIdr() {
    std::shared_ptr<ScrcpyControlClient> control;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        control = control_;
    }
    if (control) control->RequestIdr();
}

SourceStatus ScrcpySource::Status() const {
    std::lock_guard<std::mutex> lock(mutex_);
    SourceHealthState state = SourceHealthState::Disconnected;
    if (everConnected_.load()) {
        auto idle = std::chrono::steady_clock::now() - lastFrameAt_.load();
        state = idle > stallThreshold_ ? SourceHealthState::Stalled
                                        : SourceHealthState::Connected;
    }
    return SourceStatus{state, generation_, width_, height_};
}

std::shared_ptr<ScrcpyControlClient> ScrcpySource::Control() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return control_;
}
```

Note: `ConnectInitial`'s "Connected" status immediately after a successful
handshake (before any frame has actually arrived) relies on
`lastFrameAt_` being initialized to "now" at construction/(re)connect time,
so the stall clock starts from connect, not from an undefined first-frame
time. This is intentional — re-verify this behavior against
`ScrcpySource.ConnectInitialSucceedsAndReportsDimensions`'s expectation of
`Connected` immediately after connect, with no frames sent by the fake
server in that test.

Add `src/scrcpy_source.cpp` to `engine_core` in `engine/CMakeLists.txt`.

- [ ] **Step 4: Add control-send error detection**

The spec requires "control-send error detection" as part of making the
scrcpy clients replaceable-as-one-generation. `ScrcpyControlClient::Send`
(in the existing, unmodified-until-now `engine/src/scrcpy_control.cpp`)
currently calls `send()` and discards its return value — a dead control
socket (e.g. scrcpy-server crashed) fails silently forever. Add a
same-shaped failure signal without changing any existing public method's
signature (so `test_scrcpy_control.cpp`'s existing assertions keep
compiling unchanged):

Append to `engine/test/test_scrcpy_control.cpp` (read the file first to
match its existing fixture/style before appending):

```cpp
TEST(ScrcpyControlClient, LastSendFailedReflectsAFailedSend) {
    ScrcpyControlClient control(1); // no listener on this port
    control.Connect(); // connect() itself may fail synchronously depending
                        // on platform timing against a closed port; either
                        // way, IsConnected()/subsequent Send() must not
                        // silently report success.
    EXPECT_FALSE(control.LastSendFailed()); // no send attempted yet
    control.RequestIdr(); // Send() on a socket with nothing listening
    EXPECT_TRUE(control.LastSendFailed());
}
```

Run it on Windows: expected FAIL — `LastSendFailed` not declared yet.

Add to `engine/src/scrcpy_control.h`'s public section:

```cpp
    // True once any Send-based call (SendTouch/SendKeycode/RequestIdr) has
    // failed at the socket layer since construction or since a caller
    // resets it. Existing methods keep their void signatures — this is an
    // additive observability seam, not a behavior change to the send path.
    bool LastSendFailed() const;
    void ResetSendFailureFlag();
```

In `engine/src/scrcpy_control.cpp`'s `Impl`, add
`std::atomic<bool> lastSendFailed{false};` and change `Impl::Send` to
record a failure:

```cpp
    void Send(const std::vector<uint8_t>& msg) {
        std::lock_guard<std::mutex> lock(sendMutex);
        if (sock == INVALID_SOCKET) { lastSendFailed = true; return; }
        int result = send(sock, reinterpret_cast<const char*>(msg.data()), static_cast<int>(msg.size()), 0);
        if (result == SOCKET_ERROR) lastSendFailed = true;
    }
```

Add the two new method definitions at the bottom of the file:

```cpp
bool ScrcpyControlClient::LastSendFailed() const { return impl_->lastSendFailed.load(); }
void ScrcpyControlClient::ResetSendFailureFlag() { impl_->lastSendFailed.store(false); }
```

Wire this into `ScrcpySource::Status()`: a control send failure escalates
health to `Disconnected` immediately, without waiting for the frame-stall
timer (a dead control socket usually means the whole scrcpy-server process
is gone, and frames may keep trickling from OS buffers briefly). Update
`ScrcpySource::Status()`'s body:

```cpp
SourceStatus ScrcpySource::Status() const {
    std::lock_guard<std::mutex> lock(mutex_);
    SourceHealthState state = SourceHealthState::Disconnected;
    if (everConnected_.load()) {
        bool controlFailed = control_ && control_->LastSendFailed();
        auto idle = std::chrono::steady_clock::now() - lastFrameAt_.load();
        if (controlFailed) {
            state = SourceHealthState::Disconnected;
        } else {
            state = idle > stallThreshold_ ? SourceHealthState::Stalled
                                            : SourceHealthState::Connected;
        }
    }
    return SourceStatus{state, generation_, width_, height_};
}
```

And call `control_->ResetSendFailureFlag()` right after constructing the
new `control` in `ConnectGenerationLocked`, immediately before assigning
`control_ = std::move(control);` — each new generation starts clean.

Append to `engine/test/test_scrcpy_source.cpp`:

```cpp
TEST(ScrcpySource, ControlSendFailureEscalatesToDisconnected) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());
    ASSERT_EQ(source.Status().state, SourceHealthState::Connected);

    fake.Stop(); // closes the control socket out from under the client
    source.RequestIdr(); // send on the now-dead socket
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    EXPECT_EQ(source.Status().state, SourceHealthState::Disconnected);
}
```

Run:

```powershell
cmake --build engine\build --config Release --target engine_tests
.\engine\build\Release\engine_tests.exe --gtest_filter="ScrcpyControlClient.LastSendFailedReflectsAFailedSend:ScrcpySource.ControlSendFailureEscalatesToDisconnected"
```
Expected: both pass.

- [ ] **Step 5: Build and run everything from this task**

```powershell
cmake --build engine\build --config Release --target engine_tests
.\engine\build\Release\engine_tests.exe --gtest_filter="SpsPpsCache.ResetClearsStoredConfig:ScrcpySource.*:ScrcpyControlClient.*"
```
Expected: all pass.

- [ ] **Step 6: Run the full existing suite for a regression check**

```powershell
.\engine\build\Release\engine_tests.exe --gtest_filter=-SignalingClient.*
```
Expected: everything still green (this task changed `h264_nalu.h`/`.cpp`
and `scrcpy_control.h`/`.cpp` only additively).

- [ ] **Step 7: Commit**

```bash
git add engine/src/h264_nalu.h engine/src/h264_nalu.cpp engine/test/test_h264_nalu.cpp \
  engine/src/scrcpy_source.h engine/src/scrcpy_source.cpp engine/test/test_scrcpy_source.cpp \
  engine/src/scrcpy_control.h engine/src/scrcpy_control.cpp engine/test/test_scrcpy_control.cpp \
  engine/CMakeLists.txt
git commit -m "feat(engine): add ScrcpySource generation-based reconnect and health state"
```

---

### Task 6: Admin HTTP endpoints (health, reconnect, keyframe)

**Files:**
- Create: `engine/src/admin_handler.h`
- Create: `engine/src/admin_handler.cpp`
- Create: `engine/test/test_admin_handler.cpp`
- Modify: `engine/CMakeLists.txt`

**Interfaces:**
- Consumes: `ScrcpySource` (Task 5), `EngineHttpServer` (Task 1).
- Produces for Task 9:
  ```cpp
  class AdminHandler {
  public:
      explicit AdminHandler(ScrcpySource& source);
      void RegisterRoutes(httplib::Server& server);
  };
  ```
- Routes: `GET /admin/health` → `{state, generation, width, height}` (JSON,
  `state` as the string `"connected"`/`"disconnected"`/`"stalled"`);
  `POST /admin/reconnect` with body `{"scrcpy_port": N, "generation": N}` →
  200 with `{"accepted": true, "generation": N}` or 409 with
  `{"accepted": false, "current_generation": N}` for a stale generation;
  `POST /admin/keyframe` → 204, calls `source.RequestIdr()`.

- [ ] **Step 1: Write the failing tests**

Create `engine/test/test_admin_handler.cpp`. Reuses the `FakeScrcpyServer`
pattern from Task 5's test — pull it into a small shared test header
first:

Create `engine/test/fake_scrcpy_server.h` by moving the `FakeScrcpyServer`
class body out of `test_scrcpy_source.cpp` into this new header verbatim
(same class, unchanged), then replace `test_scrcpy_source.cpp`'s inline
definition with `#include "fake_scrcpy_server.h"`.

```cpp
#include <gtest/gtest.h>
#include "admin_handler.h"
#include "scrcpy_source.h"
#include "peer_registry.h"
#include "http_server.h"
#include "fake_scrcpy_server.h"
#include <httplib.h>
#include <nlohmann/json.hpp>

using json = nlohmann::json;

TEST(AdminHandler, HealthReflectsSourceStatus) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());

    AdminHandler handler(source);
    EngineHttpServer server("127.0.0.1");
    handler.RegisterRoutes(server.Server());
    server.Start();

    httplib::Client client("127.0.0.1", server.Port());
    auto res = client.Get("/admin/health");
    ASSERT_TRUE(res);
    EXPECT_EQ(res->status, 200);
    auto body = json::parse(res->body);
    EXPECT_EQ(body["state"], "connected");
    EXPECT_EQ(body["width"], 100);
    EXPECT_EQ(body["height"], 200);

    server.Stop();
    fake.Stop();
}

TEST(AdminHandler, ReconnectAcceptsNewerGenerationAndRejectsStale) {
    FakeScrcpyServer fake1;
    fake1.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake1.Port());

    AdminHandler handler(source);
    EngineHttpServer server("127.0.0.1");
    handler.RegisterRoutes(server.Server());
    server.Start();

    FakeScrcpyServer fake2;
    fake2.Serve();
    httplib::Client client("127.0.0.1", server.Port());

    json staleBody = {{"scrcpy_port", fake2.Port()}, {"generation", 0}};
    auto staleRes = client.Post("/admin/reconnect", staleBody.dump(), "application/json");
    ASSERT_TRUE(staleRes);
    EXPECT_EQ(staleRes->status, 409);

    json freshBody = {{"scrcpy_port", fake2.Port()}, {"generation", 1}};
    auto freshRes = client.Post("/admin/reconnect", freshBody.dump(), "application/json");
    ASSERT_TRUE(freshRes);
    EXPECT_EQ(freshRes->status, 200);
    EXPECT_EQ(source.Status().generation, 1u);

    server.Stop();
    fake1.Stop();
    fake2.Stop();
}

TEST(AdminHandler, KeyframeReturns204) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());

    AdminHandler handler(source);
    EngineHttpServer server("127.0.0.1");
    handler.RegisterRoutes(server.Server());
    server.Start();

    httplib::Client client("127.0.0.1", server.Port());
    auto res = client.Post("/admin/keyframe", "", "application/json");
    ASSERT_TRUE(res);
    EXPECT_EQ(res->status, 204);

    server.Stop();
    fake.Stop();
}
```

Add `test/test_admin_handler.cpp` and `test/fake_scrcpy_server.h` (header
only, not compiled standalone) to `engine/CMakeLists.txt`'s `engine_tests`
sources; also update `test/test_scrcpy_source.cpp` in place to
`#include "fake_scrcpy_server.h"` and delete its now-duplicated inline
`FakeScrcpyServer` definition.

- [ ] **Step 2: Verify red state, then implement**

```powershell
cmake --build engine\build --config Release --target engine_tests
```
Expected: fails — `admin_handler.h` doesn't exist.

Create `engine/src/admin_handler.h`:

```cpp
#pragma once
#include "scrcpy_source.h"
#include <httplib.h>

class AdminHandler {
public:
    explicit AdminHandler(ScrcpySource& source);
    void RegisterRoutes(httplib::Server& server);

private:
    ScrcpySource& source_;
};
```

Create `engine/src/admin_handler.cpp`:

```cpp
#include "admin_handler.h"
#include <nlohmann/json.hpp>

using json = nlohmann::json;

namespace {
std::string StateToString(SourceHealthState state) {
    switch (state) {
        case SourceHealthState::Connected: return "connected";
        case SourceHealthState::Stalled: return "stalled";
        default: return "disconnected";
    }
}
}

AdminHandler::AdminHandler(ScrcpySource& source) : source_(source) {}

void AdminHandler::RegisterRoutes(httplib::Server& server) {
    server.Get("/admin/health", [this](const httplib::Request&, httplib::Response& res) {
        auto status = source_.Status();
        json body = {
            {"state", StateToString(status.state)},
            {"generation", status.generation},
            {"width", status.width},
            {"height", status.height},
        };
        res.set_content(body.dump(), "application/json");
    });

    server.Post("/admin/reconnect", [this](const httplib::Request& req, httplib::Response& res) {
        auto body = json::parse(req.body, nullptr, false);
        if (body.is_discarded() || !body.contains("scrcpy_port") || !body.contains("generation")) {
            res.status = 400;
            return;
        }
        int port = body["scrcpy_port"].get<int>();
        std::uint64_t generation = body["generation"].get<std::uint64_t>();

        bool accepted = source_.Reconnect(port, generation);
        res.status = accepted ? 200 : 409;
        json responseBody = accepted
            ? json{{"accepted", true}, {"generation", generation}}
            : json{{"accepted", false}, {"current_generation", source_.Status().generation}};
        res.set_content(responseBody.dump(), "application/json");
    });

    server.Post("/admin/keyframe", [this](const httplib::Request&, httplib::Response& res) {
        source_.RequestIdr();
        res.status = 204;
    });
}
```

Add `src/admin_handler.cpp` to `engine_core` in `engine/CMakeLists.txt`.

- [ ] **Step 3: Build and run**

```powershell
cmake --build engine\build --config Release --target engine_tests
.\engine\build\Release\engine_tests.exe --gtest_filter="AdminHandler.*:ScrcpySource.*"
```
Expected: all pass (re-running `ScrcpySource.*` here confirms the
`fake_scrcpy_server.h` extraction didn't break Task 5's tests).

- [ ] **Step 4: Commit**

```bash
git add engine/src/admin_handler.h engine/src/admin_handler.cpp \
  engine/test/test_admin_handler.cpp engine/test/fake_scrcpy_server.h \
  engine/test/test_scrcpy_source.cpp engine/CMakeLists.txt
git commit -m "feat(engine): add loopback admin endpoints for health/reconnect/keyframe"
```

---

### Task 7: VPS signaling — raw-SDP, persistent, single public peer

**Files:**
- Create: `engine/src/public_signaling.h`
- Create: `engine/src/public_signaling.cpp`
- Create: `engine/test/test_public_signaling.cpp`
- Modify: `engine/CMakeLists.txt`

**Interfaces:**
- Consumes: `SignalingClient` (existing, unchanged — its `Connect`/`Send`/
  `Disconnect`/`IsConnected` API and raw-text transport are reused as-is),
  `PeerRegistry`/`PeerSession` (Tasks 2/3).
- Produces for Task 9:
  ```cpp
  class PublicSignalingBridge {
  public:
      PublicSignalingBridge(SignalingClient& signaling, PeerRegistry& registry,
                             std::vector<std::string> iceServers);

      // Registers signaling.Connect()'s message handler. Each received
      // text message is treated as a complete raw SDP offer: creates (or
      // replaces, via PeerRegistry's own single-public-peer rule) the
      // public PeerSession, and sends the raw SDP answer back over
      // signaling. Malformed input (fails AnswerOffer) is logged and
      // dropped — no reply is sent, so a confused/hostile viewer does not
      // get a partial/error answer that could be mistaken for real SDP.
      void Start();
  };
  ```
- No JSON envelope, no ICE-candidate messages — both are non-trickle so
  candidates are embedded in the one offer/answer already exchanged.

- [ ] **Step 1: Write the failing test**

Create `engine/test/test_public_signaling.cpp`. This test needs a real
signaling server; per `engine/test/README.md`'s existing convention for
`test_signaling_client.cpp`, it assumes one is already running at
`ws://localhost:8443` with JWT disabled:

```cpp
#include <gtest/gtest.h>
#include "public_signaling.h"
#include "peer_registry.h"
#include "signaling_client.h"
#include <rtc/rtc.hpp>
#include <atomic>
#include <chrono>
#include <thread>

namespace {
std::string GatheredOffer() {
    rtc::PeerConnection pc;
    pc.addTransceiver(rtc::Description::Media::Kind::Video,
                       rtc::Description::Direction::RecvOnly);
    pc.createDataChannel("input");
    std::atomic<bool> gathered{false};
    pc.onGatheringStateChange([&](rtc::PeerConnection::GatheringState s) {
        if (s == rtc::PeerConnection::GatheringState::Complete) gathered = true;
    });
    pc.setLocalDescription();
    for (int i = 0; i < 200 && !gathered; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(25));
    return std::string(*pc.localDescription());
}
}

TEST(PublicSignalingBridge, AnswersRawSdpOfferWithRawSdpAnswer) {
    SignalingClient engineSide("ws://localhost:8443", "test-public-1", "engine", "");
    SignalingClient viewerSide("ws://localhost:8443", "test-public-1", "viewer", "");

    PeerRegistry registry;
    PublicSignalingBridge bridge(engineSide, registry, {});
    bridge.Start();

    std::atomic<bool> gotAnswer{false};
    std::string answerSdp;
    viewerSide.Connect([&](const std::string& msg) {
        answerSdp = msg;
        gotAnswer = true;
    });

    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    ASSERT_TRUE(viewerSide.IsConnected());

    viewerSide.Send(GatheredOffer());

    for (int i = 0; i < 400 && !gotAnswer; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(25));
    ASSERT_TRUE(gotAnswer);
    EXPECT_NE(answerSdp.find("v=0"), std::string::npos);
    EXPECT_EQ(answerSdp.find('{'), std::string::npos); // raw SDP, not JSON-wrapped
    EXPECT_TRUE(registry.HasPublicPeer());
}

TEST(PublicSignalingBridge, SecondOfferReplacesFirstPublicPeer) {
    SignalingClient engineSide("ws://localhost:8443", "test-public-2", "engine", "");
    SignalingClient viewerSide("ws://localhost:8443", "test-public-2", "viewer", "");

    PeerRegistry registry;
    PublicSignalingBridge bridge(engineSide, registry, {});
    bridge.Start();

    int answerCount = 0;
    viewerSide.Connect([&](const std::string&) { ++answerCount; });
    std::this_thread::sleep_for(std::chrono::milliseconds(200));

    viewerSide.Send(GatheredOffer());
    for (int i = 0; i < 200 && answerCount < 1; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(25));
    ASSERT_EQ(answerCount, 1);
    ASSERT_TRUE(registry.HasPublicPeer());

    viewerSide.Send(GatheredOffer());
    for (int i = 0; i < 200 && answerCount < 2; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(25));
    EXPECT_EQ(answerCount, 2);
    EXPECT_TRUE(registry.HasPublicPeer());
}
```

Add `test/test_public_signaling.cpp` to `engine_tests`.

- [ ] **Step 2: Verify red state, then implement**

```powershell
cmake --build engine\build --config Release --target engine_tests
```
Expected: fails — `public_signaling.h` doesn't exist.

Create `engine/src/public_signaling.h`:

```cpp
#pragma once
#include "peer_registry.h"
#include "signaling_client.h"
#include <string>
#include <vector>

class PublicSignalingBridge {
public:
    PublicSignalingBridge(SignalingClient& signaling, PeerRegistry& registry,
                           std::vector<std::string> iceServers);
    void Start();

private:
    SignalingClient& signaling_;
    PeerRegistry& registry_;
    std::vector<std::string> iceServers_;
    int nextPublicSeq_ = 0;
};
```

Create `engine/src/public_signaling.cpp`:

```cpp
#include "public_signaling.h"
#include <iostream>

PublicSignalingBridge::PublicSignalingBridge(
    SignalingClient& signaling, PeerRegistry& registry, std::vector<std::string> iceServers)
    : signaling_(signaling), registry_(registry), iceServers_(std::move(iceServers)) {}

void PublicSignalingBridge::Start() {
    signaling_.Connect([this](const std::string& rawSdpOffer) {
        std::string id = "public-" + std::to_string(++nextPublicSeq_);
        auto session = registry_.Create(PeerKind::Public, id, iceServers_);
        if (!session) {
            std::cerr << "[public_signaling] failed to create public peer slot" << std::endl;
            return;
        }
        try {
            std::string answer = session->AnswerOffer(rawSdpOffer);
            signaling_.Send(answer);
        } catch (const std::exception& e) {
            std::cerr << "[public_signaling] AnswerOffer failed, dropping: " << e.what() << std::endl;
            registry_.Remove(id);
        }
    });
}
```

Add `src/public_signaling.cpp` to `engine_core` in `engine/CMakeLists.txt`.

- [ ] **Step 3: Build and run**

Requires a local signaling server (per `engine/test/README.md`'s existing
instructions for `SignalingClient.*`):

```powershell
cmake --build engine\build --config Release --target engine_tests
.\engine\build\Release\engine_tests.exe --gtest_filter="PublicSignalingBridge.*"
```
Expected: both pass. (These share the "requires a live local signaling
server" prerequisite as `SignalingClient.*` — exclude with
`-PublicSignalingBridge.*:SignalingClient.*` in any run without one.)

- [ ] **Step 4: Commit**

```bash
git add engine/src/public_signaling.h engine/src/public_signaling.cpp \
  engine/test/test_public_signaling.cpp engine/CMakeLists.txt
git commit -m "feat(engine): rewrite VPS signaling to raw-SDP viewer-offerer protocol"
```

---

### Task 8: Canonical input protocol — per-peer InputRouter

**Files:**
- Create: `engine/src/input_router.h`
- Create: `engine/src/input_router.cpp`
- Create: `engine/test/test_input_router.cpp`
- Modify: `engine/CMakeLists.txt`

**Interfaces:**
- Consumes: `ScrcpyControlClient` (existing), `ScrcpySource::Control()`/
  `ScrcpySource::Status()` (Task 5, for live width/height and the current
  control client), `PeerSession::SetInputCallback`/`SetOnStateChange`
  (Task 2).
- Produces for Task 9:
  ```cpp
  class InputRouter {
  public:
      explicit InputRouter(ScrcpySource& source,
                            std::chrono::milliseconds idrRateLimit =
                                std::chrono::milliseconds(2000));

      // Call once per peer, right after PeerSession is created, before
      // AnswerOffer — wires this peer's DataChannel input directly.
      void AttachToPeer(PeerSession& peer);
  };
  ```
- Canonical message shapes handled (verbatim client protocol, not the
  prototype's `tap`/`swipe`/`key`):
  `{"type":"click","x":0.5,"y":0.5}`,
  `{"type":"drag_start","x":..,"y":..}`, `{"type":"drag_move","x":..,"y":..}`,
  `{"type":"drag_end","x":..,"y":..}`, `{"type":"scroll","x":..,"y":..,"dy":..}`
  (scroll cancellation: a `scroll` event sends its own DOWN/MOVE/UP triplet
  at fixed short intervals — treat identically to a fast drag for this
  plan's C++ scope, matching drag's action-code usage),
  `{"type":"key","key":"ArrowLeft"}`, `{"type":"idr"}`,
  `{"type":"echo","...":"..."}` (reflected verbatim).

- [ ] **Step 1: Write the failing tests**

Create `engine/test/test_input_router.cpp`. Uses a fake
`ScrcpyControlClient`-compatible sink via a thin seam: since
`ScrcpyControlClient`'s methods aren't virtual, this test drives
`InputRouter` against a real `ScrcpySource` connected to the
`FakeScrcpyServer` from Task 5/6 and inspects effects by having the fake
server also record raw bytes received on its control connection:

```cpp
#include <gtest/gtest.h>
#include "input_router.h"
#include "scrcpy_source.h"
#include "peer_registry.h"
#include "fake_scrcpy_server.h"
#include <rtc/rtc.hpp>
#include <atomic>
#include <chrono>
#include <thread>

TEST(InputRouter, ClickSendsDownThenUpTouchPair) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());

    InputRouter router(source);

    rtc::PeerConnection viewerPc;
    auto inputChannel = viewerPc.createDataChannel("input");
    viewerPc.addTransceiver(rtc::Description::Media::Kind::Video,
                             rtc::Description::Direction::RecvOnly);
    std::atomic<bool> gathered{false};
    viewerPc.onGatheringStateChange([&](rtc::PeerConnection::GatheringState s) {
        if (s == rtc::PeerConnection::GatheringState::Complete) gathered = true;
    });
    viewerPc.setLocalDescription();
    for (int i = 0; i < 200 && !gathered; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(25));

    auto session = registry.Create(PeerKind::Local, "input-test-1", {});
    router.AttachToPeer(*session);
    std::string answer = session->AnswerOffer(std::string(*viewerPc.localDescription()));
    viewerPc.setRemoteDescription(rtc::Description(answer, "answer"));

    std::atomic<bool> dcOpen{false};
    inputChannel->onOpen([&]() { dcOpen = true; });
    for (int i = 0; i < 200 && !dcOpen; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(25));
    ASSERT_TRUE(dcOpen);

    inputChannel->send(std::string(R"({"type":"click","x":0.5,"y":0.5})"));
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // 2 touch messages (DOWN + UP), each INJECT_TOUCH_EVENT is 32 bytes per
    // scrcpy_control.cpp's SendTouch — assert on byte count as a proxy for
    // "exactly two touch events were sent," matching this plan's existing
    // wire-format tests' style rather than re-parsing the binary protocol.
    EXPECT_EQ(fake.ControlBytesReceived(), 64u);

    fake.Stop();
}

TEST(InputRouter, UnknownKeyNameIsIgnoredWithoutCrashing) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());
    InputRouter router(source);

    // HandleMessageForTest is InputRouter's test-only direct entry point —
    // equivalent to what a real DataChannel message delivers, without
    // requiring a PeerSession/full WebRTC negotiation just to prove an
    // unrecognized key name is a no-op, not a crash.
    ASSERT_NO_THROW(router.HandleMessageForTest(R"({"type":"key","key":"NotARealKey"})"));
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    EXPECT_EQ(fake.ControlBytesReceived(), 0u);

    fake.Stop();
}

TEST(InputRouter, RateLimitsRapidIdrRequests) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());
    InputRouter router(source, /*idrRateLimit=*/std::chrono::milliseconds(200));

    // Two idr requests in rapid succession — the second must be dropped by
    // the rate limit.
    router.HandleMessageForTest(R"({"type":"idr"})");
    router.HandleMessageForTest(R"({"type":"idr"})");
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    EXPECT_EQ(fake.ControlBytesReceived(), 1u); // one TYPE_RESET_VIDEO byte

    // A third request after the rate-limit window must go through.
    std::this_thread::sleep_for(std::chrono::milliseconds(250));
    router.HandleMessageForTest(R"({"type":"idr"})");
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
    EXPECT_EQ(fake.ControlBytesReceived(), 2u);

    fake.Stop();
}

TEST(InputRouter, EchoIsReflectedVerbatimOnSamePeer) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());
    InputRouter router(source);

    rtc::PeerConnection viewerPc;
    auto inputChannel = viewerPc.createDataChannel("input");
    viewerPc.addTransceiver(rtc::Description::Media::Kind::Video,
                             rtc::Description::Direction::RecvOnly);
    std::atomic<bool> gathered{false};
    viewerPc.onGatheringStateChange([&](rtc::PeerConnection::GatheringState s) {
        if (s == rtc::PeerConnection::GatheringState::Complete) gathered = true;
    });
    viewerPc.setLocalDescription();
    for (int i = 0; i < 200 && !gathered; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(25));

    auto session = registry.Create(PeerKind::Local, "input-test-4", {});
    router.AttachToPeer(*session);
    std::string answer = session->AnswerOffer(std::string(*viewerPc.localDescription()));
    viewerPc.setRemoteDescription(rtc::Description(answer, "answer"));

    std::atomic<bool> dcOpen{false};
    inputChannel->onOpen([&]() { dcOpen = true; });
    for (int i = 0; i < 200 && !dcOpen; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(25));

    std::atomic<bool> gotEcho{false};
    std::string echoBody;
    inputChannel->onMessage([&](rtc::message_variant data) {
        if (std::holds_alternative<std::string>(data)) {
            echoBody = std::get<std::string>(data);
            gotEcho = true;
        }
    });

    inputChannel->send(std::string(R"({"type":"echo","t":123})"));
    for (int i = 0; i < 200 && !gotEcho; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(25));
    ASSERT_TRUE(gotEcho);
    EXPECT_EQ(echoBody, R"({"type":"echo","t":123})");

    fake.Stop();
}
```

Also extend `engine/test/fake_scrcpy_server.h`'s `FakeScrcpyServer` with a
`ControlBytesReceived()` accessor: have the control-connection accept loop
(currently just stored and left idle in Task 5/6's version) spawn its own
reader thread that counts bytes received into an `std::atomic<size_t>`,
returned by this new method.

- [ ] **Step 2: Verify red state, then implement**

```powershell
cmake --build engine\build --config Release --target engine_tests
```
Expected: fails — `input_router.h` doesn't exist (and
`ControlBytesReceived` doesn't exist on `FakeScrcpyServer` yet — add it as
part of this step alongside `input_router.h`, since the test file needs
both simultaneously).

Create `engine/src/input_router.h`:

```cpp
#pragma once
#include "peer_session.h"
#include "scrcpy_source.h"
#include <chrono>
#include <cstdint>
#include <map>
#include <mutex>
#include <string>

class InputRouter {
public:
    explicit InputRouter(ScrcpySource& source,
                          std::chrono::milliseconds idrRateLimit =
                              std::chrono::milliseconds(2000));

    void AttachToPeer(PeerSession& peer);

    // Test-only direct entry point equivalent to what a real DataChannel
    // message delivers — avoids requiring a full WebRTC negotiation in
    // tests that only care about message handling, not transport.
    void HandleMessageForTest(const std::string& jsonMessage);

private:
    struct FingerState {
        bool down = false;
        std::uint64_t pointerId = 0;
    };

    void HandleMessage(PeerSession* peer, const std::string& jsonMessage);
    std::int32_t KeycodeForKey(const std::string& key) const;

    ScrcpySource& source_;
    std::chrono::milliseconds idrRateLimit_;
    std::chrono::steady_clock::time_point lastIdrRequest_{};
    std::mutex fingerMutex_;
    std::map<PeerSession*, FingerState> fingerStates_;
};
```

Create `engine/src/input_router.cpp`. The key-name table is copied
verbatim from `src/server/app.py`'s `_JS_KEY_TO_KEYCODE` so both sides
agree on every mapping:

```cpp
#include "input_router.h"
#include <nlohmann/json.hpp>
#include <iostream>
#include <unordered_map>

using json = nlohmann::json;

namespace {

const std::unordered_map<std::string, std::int32_t>& KeyTable() {
    static const std::unordered_map<std::string, std::int32_t> table = {
        {"Return", 66}, {"BackSpace", 67}, {"Tab", 61}, {"Escape", 111},
        {"Delete", 112}, {"ArrowLeft", 21}, {"ArrowUp", 19}, {"ArrowRight", 22},
        {"ArrowDown", 20}, {" ", 62}, {"Space", 62}, {"Back", 4}, {"Home", 3},
        {"Menu", 82},
    };
    return table;
}

} // namespace

InputRouter::InputRouter(ScrcpySource& source, std::chrono::milliseconds idrRateLimit)
    : source_(source), idrRateLimit_(idrRateLimit) {}

void InputRouter::AttachToPeer(PeerSession& peer) {
    peer.SetInputCallback([this, &peer](const std::string& jsonMessage) {
        HandleMessage(&peer, jsonMessage);
    });
    peer.SetOnStateChange([this, &peer](rtc::PeerConnection::State state) {
        if (state != rtc::PeerConnection::State::Disconnected &&
            state != rtc::PeerConnection::State::Failed &&
            state != rtc::PeerConnection::State::Closed) {
            return;
        }
        // Best-effort UP so one abruptly-disconnected viewer's held-down
        // finger doesn't stay stuck for whoever connects next.
        std::lock_guard<std::mutex> lock(fingerMutex_);
        auto it = fingerStates_.find(&peer);
        if (it != fingerStates_.end() && it->second.down) {
            if (auto control = source_.Control()) {
                auto status = source_.Status();
                control->SendTouch(ScrcpyControlClient::ACTION_UP, 0, 0,
                                    status.width, status.height, it->second.pointerId);
            }
        }
        fingerStates_.erase(it);
    });
}

void InputRouter::HandleMessageForTest(const std::string& jsonMessage) {
    HandleMessage(nullptr, jsonMessage);
}

std::int32_t InputRouter::KeycodeForKey(const std::string& key) const {
    auto it = KeyTable().find(key);
    return it != KeyTable().end() ? it->second : 0;
}

void InputRouter::HandleMessage(PeerSession* peer, const std::string& jsonMessage) {
    auto msg = json::parse(jsonMessage, nullptr, false);
    if (msg.is_discarded()) return;
    std::string type = msg.value("type", "");

    if (type == "echo") {
        if (peer) peer->SendInputMessage(jsonMessage);
        return;
    }

    auto control = source_.Control();
    auto status = source_.Status();
    if (!control) return;

    double x = msg.value("x", 0.0);
    double y = msg.value("y", 0.0);

    if (type == "click") {
        control->SendTouch(ScrcpyControlClient::ACTION_DOWN, x, y, status.width, status.height);
        control->SendTouch(ScrcpyControlClient::ACTION_UP, x, y, status.width, status.height);
    } else if (type == "drag_start") {
        std::lock_guard<std::mutex> lock(fingerMutex_);
        fingerStates_[peer] = FingerState{true, 0};
        control->SendTouch(ScrcpyControlClient::ACTION_DOWN, x, y, status.width, status.height);
    } else if (type == "drag_move") {
        control->SendTouch(ScrcpyControlClient::ACTION_MOVE, x, y, status.width, status.height);
    } else if (type == "drag_end") {
        {
            std::lock_guard<std::mutex> lock(fingerMutex_);
            fingerStates_.erase(peer);
        }
        control->SendTouch(ScrcpyControlClient::ACTION_UP, x, y, status.width, status.height);
    } else if (type == "scroll") {
        // Treated as a fast synthetic drag for this plan's C++ scope (see
        // Task 8's Interfaces note) — DOWN/MOVE(offset by dy)/UP.
        double dy = msg.value("dy", 0.0);
        control->SendTouch(ScrcpyControlClient::ACTION_DOWN, x, y, status.width, status.height);
        control->SendTouch(ScrcpyControlClient::ACTION_MOVE, x, y - dy, status.width, status.height);
        control->SendTouch(ScrcpyControlClient::ACTION_UP, x, y - dy, status.width, status.height);
    } else if (type == "key") {
        std::int32_t keycode = KeycodeForKey(msg.value("key", ""));
        if (keycode != 0) control->SendKeycode(keycode);
    } else if (type == "idr") {
        auto now = std::chrono::steady_clock::now();
        if (now - lastIdrRequest_ < idrRateLimit_) return;
        lastIdrRequest_ = now;
        control->RequestIdr();
    }
}
```

This requires adding one small method to `PeerSession` (Task 2's class) so
`echo` can send back over the DataChannel — add to `engine/src/peer_session.h`:

```cpp
    void SendInputMessage(const std::string& jsonMessage);
```

And to `engine/src/peer_session.cpp`'s `Impl`/class body:

```cpp
void PeerSession::SendInputMessage(const std::string& jsonMessage) {
    if (impl_->inputChannel && impl_->inputChannel->isOpen()) {
        impl_->inputChannel->send(jsonMessage);
    }
}
```

Add `src/input_router.cpp` to `engine_core`, `test/test_input_router.cpp`
to `engine_tests`, both in `engine/CMakeLists.txt`.

- [ ] **Step 3: Add `ControlBytesReceived()` to the shared fake server**

In `engine/test/fake_scrcpy_server.h`, add an `std::atomic<size_t>
controlBytesReceived_{0}` member and a `size_t ControlBytesReceived()
const` accessor; in the `Serve()` thread, after accepting `controlConn`,
spawn a second small thread that loops `recv()`-ing into a scratch buffer
and adding the byte count to `controlBytesReceived_` until the socket
closes, joined in `Stop()`.

- [ ] **Step 4: Build and run**

```powershell
cmake --build engine\build --config Release --target engine_tests
.\engine\build\Release\engine_tests.exe --gtest_filter="InputRouter.*"
```
Expected: all 4 pass.

- [ ] **Step 5: Commit**

```bash
git add engine/src/input_router.h engine/src/input_router.cpp \
  engine/src/peer_session.h engine/src/peer_session.cpp \
  engine/test/test_input_router.cpp engine/test/fake_scrcpy_server.h \
  engine/CMakeLists.txt
git commit -m "feat(engine): port canonical input protocol to per-peer DataChannels"
```

---

### Task 9: main.cpp rewrite — wire everything, ready record, event loop

**Files:**
- Modify: `engine/src/main.cpp` (full rewrite)
- Create: `engine/test/test_main_wiring.cpp` (tests the pieces main.cpp
  composes, not `main()` itself — `main()` stays untested by GTest, same
  as today)
- Modify: `engine/CMakeLists.txt`

**Interfaces:**
- Consumes everything from Tasks 1–8.
- New CLI contract (replaces the old 4-positional-arg form):
  ```
  engine.exe <instance_name> <scrcpy_port>
  Environment variables:
    ENGINE_WHEP_CAPABILITY_SECRET   (empty/unset => WHEP auth disabled)
    ENGINE_LOCAL_ICE_SERVERS        (comma-separated stun:/turn: URLs, may be empty)
    ENGINE_SIGNALING_URL            (empty/unset => public/VPS path disabled)
    ENGINE_SIGNALING_TOKEN          (JWT, empty if signaling auth disabled)
    ENGINE_PUBLIC_ICE_SERVERS       (comma-separated, used only if signaling enabled)
  ```
  This matches the spec's "Python passes both structured ICE configs to
  the engine by environment or protected startup input, never by a
  loggable command-line credential" and "the engine token is supplied by
  environment."

- [ ] **Step 1: Write a focused wiring test**

Create `engine/test/test_main_wiring.cpp`. `main.cpp` itself is a thin
composition root (per this plan's design, not independently unit-tested,
matching the existing file's status), but the CLI/env parsing it needs is
extracted into a small testable free function first:

```cpp
#include <gtest/gtest.h>
#include "engine_config.h"
#include <cstdlib>

TEST(EngineConfig, ParsesCommaSeparatedIceServers) {
    auto servers = ParseCommaSeparatedList("stun:a.example:3478,turn:b.example:3478");
    ASSERT_EQ(servers.size(), 2u);
    EXPECT_EQ(servers[0], "stun:a.example:3478");
    EXPECT_EQ(servers[1], "turn:b.example:3478");
}

TEST(EngineConfig, EmptyStringYieldsEmptyList) {
    EXPECT_TRUE(ParseCommaSeparatedList("").empty());
}

TEST(EngineConfig, SinglePlainEntryYieldsOneElement) {
    auto servers = ParseCommaSeparatedList("stun:only.example:3478");
    ASSERT_EQ(servers.size(), 1u);
    EXPECT_EQ(servers[0], "stun:only.example:3478");
}
```

Add `test/test_main_wiring.cpp` to `engine_tests`.

- [ ] **Step 2: Verify red state, then implement `engine_config`**

```powershell
cmake --build engine\build --config Release --target engine_tests
```
Expected: fails — `engine_config.h` doesn't exist.

Create `engine/src/engine_config.h`:

```cpp
#pragma once
#include <string>
#include <vector>

std::vector<std::string> ParseCommaSeparatedList(const std::string& csv);
```

Create `engine/src/engine_config.cpp`:

```cpp
#include "engine_config.h"
#include <sstream>

std::vector<std::string> ParseCommaSeparatedList(const std::string& csv) {
    std::vector<std::string> result;
    std::stringstream ss(csv);
    std::string item;
    while (std::getline(ss, item, ',')) {
        if (!item.empty()) result.push_back(item);
    }
    return result;
}
```

Add `src/engine_config.cpp` to `engine_core` in `engine/CMakeLists.txt`.

- [ ] **Step 3: Build and run**

```powershell
cmake --build engine\build --config Release --target engine_tests
.\engine\build\Release\engine_tests.exe --gtest_filter="EngineConfig.*"
```
Expected: all 3 pass.

- [ ] **Step 4: Rewrite `main.cpp`**

Replace `engine/src/main.cpp` in full:

```cpp
// engine/src/main.cpp
#include "admin_handler.h"
#include "engine_config.h"
#include "http_server.h"
#include "input_router.h"
#include "peer_registry.h"
#include "public_signaling.h"
#include "ready_record.h"
#include "scrcpy_source.h"
#include "signaling_client.h"
#include "whep_capability.h"
#include "whep_handler.h"
#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <thread>

#if defined(_WIN32)
#include <process.h>
#define GetProcId() _getpid()
#else
#include <unistd.h>
#define GetProcId() getpid()
#endif

std::atomic<bool> g_running{true};
void OnSigint(int) { g_running = false; }

namespace {
std::string GetEnvOrEmpty(const char* name) {
    const char* value = std::getenv(name);
    return value ? std::string(value) : std::string();
}
}

int main(int argc, char** argv) {
    if (argc < 3) {
        std::cerr << "Usage: engine.exe <instance_name> <scrcpy_port>\n"
                     "Environment: ENGINE_WHEP_CAPABILITY_SECRET, "
                     "ENGINE_LOCAL_ICE_SERVERS, ENGINE_SIGNALING_URL, "
                     "ENGINE_SIGNALING_TOKEN, ENGINE_PUBLIC_ICE_SERVERS\n";
        return 1;
    }

    std::string instanceName = argv[1];
    int scrcpyPort = std::stoi(argv[2]);
    std::signal(SIGINT, OnSigint);

    try {
        PeerRegistry registry;
        ScrcpySource source(registry);
        source.ConnectInitial(scrcpyPort);

        InputRouter inputRouter(source);

        WhepCapabilityConfig whepAuth{GetEnvOrEmpty("ENGINE_WHEP_CAPABILITY_SECRET"), instanceName};
        auto localIceServers = ParseCommaSeparatedList(GetEnvOrEmpty("ENGINE_LOCAL_ICE_SERVERS"));

        EngineHttpServer whepServer("0.0.0.0");
        WhepHandler whepHandler(registry, whepAuth, localIceServers);
        whepHandler.RegisterRoutes(whepServer.Server());
        whepServer.Start();

        EngineHttpServer adminServer("127.0.0.1");
        AdminHandler adminHandler(source);
        adminHandler.RegisterRoutes(adminServer.Server());
        adminServer.Start();

        std::unique_ptr<SignalingClient> signaling;
        std::unique_ptr<PublicSignalingBridge> publicBridge;
        std::string signalingUrl = GetEnvOrEmpty("ENGINE_SIGNALING_URL");
        if (!signalingUrl.empty()) {
            std::string signalingToken = GetEnvOrEmpty("ENGINE_SIGNALING_TOKEN");
            signaling = std::make_unique<SignalingClient>(
                signalingUrl, instanceName, "engine", signalingToken);
            auto publicIceServers = ParseCommaSeparatedList(GetEnvOrEmpty("ENGINE_PUBLIC_ICE_SERVERS"));
            publicBridge = std::make_unique<PublicSignalingBridge>(*signaling, registry, publicIceServers);
            publicBridge->Start();
        }

        auto status = source.Status();
        std::string ready = BuildReadyRecord(
            instanceName, GetProcId(), whepServer.Port(), adminServer.Port(),
            status.generation, status.width, status.height);
        std::cout << ready << std::endl;

        // Note: InputRouter must be attached to every peer as it's created.
        // WhepHandler and PublicSignalingBridge construct PeerSessions
        // internally, so this composition root cannot call AttachToPeer
        // directly per-peer here — the wiring lives inside those two
        // classes' Create() call sites in Tasks 4/7. Verify at build/e2e
        // time (Task 10's manual gate) that input actually reaches
        // scrcpy on both a local WHEP session and a public session; if
        // it doesn't, thread an InputRouter& (or a peer-created callback)
        // through WhepHandler's and PublicSignalingBridge's constructors
        // and call AttachToPeer right after each registry.Create() —
        // this is a known integration seam this task's automated tests
        // don't cover end-to-end, flagged here rather than silently
        // assumed correct.

        auto lastHousekeeping = std::chrono::steady_clock::now();
        while (g_running) {
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
            auto now = std::chrono::steady_clock::now();
            if (now - lastHousekeeping >= std::chrono::seconds(1)) {
                registry.ReapDeadAndStalePeers();
                lastHousekeeping = now;
            }
        }

        whepServer.Stop();
        adminServer.Stop();
        std::cout << "Stopped.\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "[FATAL] unhandled exception: " << e.what() << std::endl;
        return 1;
    } catch (...) {
        std::cerr << "[FATAL] unhandled non-std::exception (unknown type)" << std::endl;
        return 1;
    }
}
```

The inline comment above is deliberate, not a placeholder left by
mistake: `WhepHandler::Create`'s and `PublicSignalingBridge::Create`'s
call sites in Tasks 4/7 build a bare `PeerSession` via
`registry_.Create(...)` with no input wiring at all — this plan as
written has a real integration gap between Task 8's `InputRouter` and
Tasks 4/7's peer-creation call sites. Close it now, in this task, by
threading `InputRouter&` through both constructors:

Modify `engine/src/whep_handler.h`'s constructor to
`WhepHandler(PeerRegistry& registry, WhepCapabilityConfig authConfig, std::vector<std::string> iceServers, InputRouter& inputRouter)`,
store `InputRouter& inputRouter_;`, and in `whep_handler.cpp`'s `POST
/whep` handler, call `inputRouter_.AttachToPeer(*session);` immediately
after a successful `registry_.Create(...)` and before calling
`session->AnswerOffer(...)` (so the input callback is wired before any
message could possibly arrive).

Modify `engine/src/public_signaling.h`'s constructor to
`PublicSignalingBridge(SignalingClient& signaling, PeerRegistry& registry, std::vector<std::string> iceServers, InputRouter& inputRouter)`,
store `InputRouter& inputRouter_;`, and in `public_signaling.cpp`, call
`inputRouter_.AttachToPeer(*session);` right after `registry_.Create(...)`
succeeds, before `session->AnswerOffer(...)`.

Update `engine/test/test_whep_handler.cpp`'s and
`engine/test/test_public_signaling.cpp`'s `WhepHandler`/
`PublicSignalingBridge` construction call sites to pass a real
`InputRouter` instance (constructed from a `ScrcpySource` connected to
each test's own fake/local source, following the same pattern
`test_input_router.cpp` already uses) — every existing test in both files
needs this one added constructor argument; no behavioral assertions in
those files change.

Update `main.cpp`'s wiring above accordingly: construct
`WhepHandler whepHandler(registry, whepAuth, localIceServers, inputRouter);`
and `PublicSignalingBridge publicBridge(*signaling, registry, publicIceServers, inputRouter);`,
and delete the inline comment block describing the gap (it's closed now).

- [ ] **Step 5: Build**

```powershell
cmake --build engine\build --config Release --target engine engine_tests
```
Expected: both targets build. This is the first point in this plan where
the full `engine.exe` binary links again — expect real compile errors on
first attempt per `engine/BUILD_WINDOWS.md`'s standing caveat; this is not
evidence anything in this plan is wrong, it's the expected first-Windows-
compile experience for new code.

- [ ] **Step 6: Run the full unit suite**

```powershell
.\engine\build\Release\engine_tests.exe --gtest_filter=-SignalingClient.*:-PublicSignalingBridge.*
```
Expected: everything except the two live-signaling-server suites passes.
Then, with a local signaling server running (per `engine/test/README.md`):
```powershell
.\engine\build\Release\engine_tests.exe --gtest_filter="SignalingClient.*:PublicSignalingBridge.*"
```
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add engine/src/main.cpp engine/src/engine_config.h engine/src/engine_config.cpp \
  engine/src/whep_handler.h engine/src/whep_handler.cpp \
  engine/src/public_signaling.h engine/src/public_signaling.cpp \
  engine/test/test_main_wiring.cpp engine/test/test_whep_handler.cpp \
  engine/test/test_public_signaling.cpp engine/CMakeLists.txt
git commit -m "feat(engine): wire main.cpp to the new multi-peer WHEP/signaling/admin engine"
```

---

### Task 10: Delete the old offerer `WebRtcPeer`, final cleanup, manual e2e gate

**Files:**
- Delete: `engine/src/peer.h`, `engine/src/peer.cpp`
- Modify: `engine/CMakeLists.txt` (remove `src/peer.cpp` from `engine_core`)
- Modify: `engine/test/README_e2e.md` (update the manual e2e steps for the
  new CLI/env contract and WHEP-based test page flow)
- Modify: `engine/test/test_page.html` (switch from the old JSON-envelope/
  engine-offerer flow to a viewer-offerer WHEP POST, matching what
  Task 4/7's real clients will do — this is the same client-side shape the
  browser/mobile plans will implement, kept here only so this plan's own
  manual gate can exercise the finished engine end-to-end)

**Interfaces:** none new — this task only removes superseded code and
updates the manual verification harness to match every prior task's
finished interfaces.

- [ ] **Step 1: Delete the superseded offerer peer**

```bash
git rm engine/src/peer.h engine/src/peer.cpp
```

Remove `src/peer.cpp` from `engine_core`'s source list in
`engine/CMakeLists.txt` (it was already superseded by `peer_session.cpp`
in Task 2, but stayed in the build until now so Task 2–9 could each
build/test independently without a mid-plan compile break from an unused
file — nothing after Task 2 includes `peer.h`).

- [ ] **Step 2: Update `test_page.html` to a WHEP viewer-offerer flow**

Read the current `engine/test/test_page.html` in full first (it currently
implements the old engine-offerer/JSON-envelope flow this plan retires).
Rewrite its connection logic to: build an `RTCPeerConnection`, call
`addTransceiver('video', {direction: 'recvonly'})`, `createDataChannel
('input')`, `createOffer()`/`setLocalDescription()`, wait for
`iceGatheringState === 'complete'` (poll or listen for
`icegatheringstatechange`), then `POST` the complete offer SDP as the
request body (`Content-Type: application/sdp`) to
`http://<engine-host>:<whep-port>/whep`, read the `Location` response
header for later `DELETE`, and `setRemoteDescription()` with the response
body as the answer. Keep the existing video-element wiring
(`pc.ontrack`) and the tap-to-inject-input handler, but send input JSON
over the DataChannel's own `send()` instead of the old signaling-channel
JSON messages — using the canonical shapes Task 8 implements (`{"type":
"click","x":...,"y":...}` etc., not the retired `tap`/`swipe`/`key`
shapes).

- [ ] **Step 3: Update `README_e2e.md`**

Replace the CLI invocation section with the new contract:

```
engine.exe <instance_name> <scrcpy_port>

Required/optional environment variables:
  ENGINE_WHEP_CAPABILITY_SECRET   (unset = WHEP auth disabled, dev-only)
  ENGINE_LOCAL_ICE_SERVERS        (comma-separated, may be empty for pure-LAN)
  ENGINE_SIGNALING_URL            (unset = public/VPS path disabled)
  ENGINE_SIGNALING_TOKEN          (JWT, only used if ENGINE_SIGNALING_URL is set)
  ENGINE_PUBLIC_ICE_SERVERS       (comma-separated, only used if signaling enabled)
```

Update the "open test_page.html" instructions: the query string the old
`test.ps1`/CLI printed (`?signaling=...&session=...&ice=...`) is retired
along with the JSON-envelope protocol; the new `test_page.html` instead
takes the WHEP URL as its own query param (`?whep=http://localhost:<port>/whep`)
printed by `engine.exe`'s stdout ready record (`whep_port`) — update the
doc's example accordingly, and update `engine/test.ps1` similarly: replace
its `$SignalingUrl`/`$SessionId`/`$IceUrl` params and the old
`test_page.html query:` echo line with the new `<instance_name>
<scrcpy_port>` invocation and a line printing
`http://localhost:8000/test_page.html?whep=http://localhost:<port from
ready record>/whep` once `engine.exe`'s stdout ready record is observed.

- [ ] **Step 4: Full build and unit suite on Windows**

```powershell
cmake --build engine\build --config Release
.\engine\build\Release\engine_tests.exe --gtest_filter=-SignalingClient.*:-PublicSignalingBridge.*
ctest --test-dir engine\build -C Release --output-on-failure
```
Expected: clean build, full offline suite green.

- [ ] **Step 5: Manual e2e gate**

Using the updated `engine/test.ps1` and `test_page.html`: launch a real
scrcpy-server (same manual setup this plan's predecessor used), launch
`engine.exe <instance> <port>` with `ENGINE_WHEP_CAPABILITY_SECRET` unset
(dev mode), open the printed WHEP URL in `test_page.html`, and verify:

1. Engine's stdout prints the ready record JSON line before the browser
   connects.
2. Browser reaches `chrome://webrtc-internals` `framesDecoded > 0` and
   climbing, video renders (not black) — same pixel-check technique as
   the prior SPS/PPS plan's Task 2 Step 5.
3. Clicking the video injects a tap (verify via device screen reaction or
   `adb logcat` showing the injected event).
4. `POST /admin/reconnect` with a fresh generation against a newly
   relaunched scrcpy-server on a different port keeps the same browser tab
   connected (no ICE restart, no page reload) and resumes video within a
   couple seconds.
5. Opening a second browser tab against the same `/whep` URL creates a
   second peer without disturbing the first (multi-peer fan-out).

If any of 2–5 fails, stop and collect evidence (engine stdout, browser
console, `webrtc-internals` stats) rather than layering another
speculative fix — same discipline as this plan's predecessor.

- [ ] **Step 6: Commit**

```bash
git add engine/test/test_page.html engine/test/README_e2e.md engine/test.ps1 \
  engine/CMakeLists.txt
git commit -m "chore(engine): retire offerer-based peer.cpp, update manual e2e harness"
```

---

## Final verification

After all 10 tasks are committed, on the Windows Host PC:

```powershell
cmake --build engine\build --config Release
.\engine\build\Release\engine_tests.exe --gtest_filter=-SignalingClient.*:-PublicSignalingBridge.*
```
Expected: clean build, full offline suite green.

With a local signaling server running:
```powershell
.\engine\build\Release\engine_tests.exe --gtest_filter="SignalingClient.*:PublicSignalingBridge.*"
```
Expected: all pass.

Then run Task 10 Step 5's full manual e2e gate. This plan is complete when
all of the above pass — it does not by itself satisfy the parent spec's
"Verification and cutover gates" section (CI enablement, the 5-instance
performance gate, and the client/Python-integration matrix are later
plans' scope), only this plan's own engine-level acceptance.
