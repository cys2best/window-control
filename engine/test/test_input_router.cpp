#include <gtest/gtest.h>
#include "input_router.h"
#include "scrcpy_source.h"
#include "peer_registry.h"
#include "fake_scrcpy_server.h"
#include <rtc/rtc.hpp>
#include <atomic>
#include <barrier>
#include <chrono>
#include <cstdint>
#include <thread>
#include <vector>

namespace {

rtc::Configuration ManualNegotiationConfig() {
    rtc::Configuration config;
    config.disableAutoNegotiation = true;
    return config;
}

std::shared_ptr<rtc::Track> AddRecvOnlyH264Video(rtc::PeerConnection& pc) {
    rtc::Description::Video video(
        "video", rtc::Description::Direction::RecvOnly);
    video.addH264Codec(96);
    return pc.addTrack(video);
}

std::uint32_t ReadU32BE(const std::vector<std::uint8_t>& bytes, std::size_t offset) {
    return (static_cast<std::uint32_t>(bytes[offset]) << 24) |
           (static_cast<std::uint32_t>(bytes[offset + 1]) << 16) |
           (static_cast<std::uint32_t>(bytes[offset + 2]) << 8) |
           static_cast<std::uint32_t>(bytes[offset + 3]);
}

struct TouchEvent {
    std::uint8_t action;
    std::uint32_t x;
    std::uint32_t y;
    double normalizedX;
    double normalizedY;
};

std::vector<TouchEvent> DecodeTouchEvents(
    const std::vector<std::uint8_t>& bytes, int sourceWidth, int sourceHeight) {
    std::vector<TouchEvent> events;
    for (std::size_t offset = 0; offset + 32 <= bytes.size(); offset += 32) {
        const auto x = ReadU32BE(bytes, offset + 10);
        const auto y = ReadU32BE(bytes, offset + 14);
        events.push_back(TouchEvent{
            bytes[offset + 1],
            x,
            y,
            static_cast<double>(x) / sourceWidth,
            static_cast<double>(y) / sourceHeight,
        });
    }
    return events;
}

class NegotiatedInputPeer {
public:
    NegotiatedInputPeer()
        : source(registry), router(source), viewerPc(ManualNegotiationConfig()) {}

    ~NegotiatedInputPeer() {
        if (session) registry.Remove(session->Id());
        session.reset();
        fake.Stop();
    }

    bool Connect(const std::string& id) {
        fake.Serve();
        source.ConnectInitial(fake.Port());

        videoTrack = AddRecvOnlyH264Video(viewerPc);
        inputChannel = viewerPc.createDataChannel("input");
        gathered = false;
        viewerPc.onGatheringStateChange([this](rtc::PeerConnection::GatheringState state) {
            if (state == rtc::PeerConnection::GatheringState::Complete) gathered = true;
        });
        viewerPc.setLocalDescription();
        for (int i = 0; i < 200 && !gathered; ++i) {
            std::this_thread::sleep_for(std::chrono::milliseconds(25));
        }
        if (!gathered) return false;

        session = registry.Create(PeerKind::Local, id, {});
        if (!session) return false;
        router.AttachToPeer(*session);
        std::string answer = session->AnswerOffer(std::string(*viewerPc.localDescription()));
        viewerPc.setRemoteDescription(rtc::Description(answer, "answer"));

        dcOpen = false;
        inputChannel->onOpen([this]() { dcOpen = true; });
        for (int i = 0; i < 200 && !dcOpen; ++i) {
            std::this_thread::sleep_for(std::chrono::milliseconds(25));
        }
        return dcOpen;
    }

    void Send(const std::string& message) {
        inputChannel->send(message);
    }

    bool WaitForTouchEvents(std::size_t count) const {
        return PollUntil([this, count]() {
            return fake.ControlBytesReceived() >= count * 32 &&
                   fake.ControlDataReceived().size() >= count * 32;
        });
    }

    std::vector<TouchEvent> TouchEvents() const {
        const auto status = source.Status();
        return DecodeTouchEvents(
            fake.ControlDataReceived(), status.width, status.height);
    }

    std::vector<std::uint8_t> TouchActions() const {
        std::vector<std::uint8_t> actions;
        for (const auto& event : TouchEvents()) actions.push_back(event.action);
        return actions;
    }

    FakeScrcpyServer fake;
    PeerRegistry registry;
    ScrcpySource source;
    InputRouter router;
    std::atomic<bool> gathered{false};
    std::atomic<bool> dcOpen{false};
    rtc::PeerConnection viewerPc;
    std::shared_ptr<rtc::Track> videoTrack;
    std::shared_ptr<rtc::DataChannel> inputChannel;
    std::shared_ptr<PeerSession> session;
};

} // namespace

TEST(InputRouter, ClickSendsDownThenUpTouchPair) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());

    InputRouter router(source);

    rtc::PeerConnection viewerPc(ManualNegotiationConfig());
    auto videoTrack = AddRecvOnlyH264Video(viewerPc);
    auto inputChannel = viewerPc.createDataChannel("input");
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

TEST(InputRouter, ConcurrentIdrRequestsShareOneRateLimitGate) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());
    InputRouter router(source, /*idrRateLimit=*/std::chrono::seconds(5));

    constexpr int senderCount = 32;
    std::barrier startGate(senderCount);
    std::vector<std::thread> senders;
    senders.reserve(senderCount);
    for (int i = 0; i < senderCount; ++i) {
        senders.emplace_back([&]() {
            startGate.arrive_and_wait();
            router.HandleMessageForTest(R"({"type":"idr"})");
        });
    }
    for (auto& sender : senders) sender.join();

    ASSERT_TRUE(PollUntil([&]() { return fake.ControlBytesReceived() >= 1u; }));
    EXPECT_EQ(fake.ControlBytesReceived(), 1u);

    fake.Stop();
}

TEST(InputRouter, EchoIsReflectedVerbatimOnSamePeer) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());
    InputRouter router(source);

    rtc::PeerConnection viewerPc(ManualNegotiationConfig());
    auto videoTrack = AddRecvOnlyH264Video(viewerPc);
    auto inputChannel = viewerPc.createDataChannel("input");
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

TEST(InputRouter, DragStartMovesAndEndEmitOneOrderedGesture) {
    NegotiatedInputPeer peer;
    ASSERT_TRUE(peer.Connect("gesture-order"));

    peer.Send(R"({"type":"drag_start","x":0.1,"y":0.2})");
    peer.Send(R"({"type":"drag_move","x":0.4,"y":0.5})");
    peer.Send(R"({"type":"drag_end","x":0.6,"y":0.7})");

    ASSERT_TRUE(peer.WaitForTouchEvents(3));
    EXPECT_EQ(peer.TouchActions(), (std::vector<std::uint8_t>{
        ScrcpyControlClient::ACTION_DOWN,
        ScrcpyControlClient::ACTION_MOVE,
        ScrcpyControlClient::ACTION_UP,
    }));
}

TEST(InputRouter, ScrollUsesNormalizedMagnitudeAndClampsExtremeDelta) {
    NegotiatedInputPeer peer;
    ASSERT_TRUE(peer.Connect("scroll-magnitude"));

    peer.Send(R"({"type":"scroll","x":0.5,"y":0.5,"dy":0.10})");
    ASSERT_TRUE(peer.WaitForTouchEvents(3));
    EXPECT_NEAR(peer.TouchEvents()[1].normalizedY, 0.60, 0.01);

    NegotiatedInputPeer clampedPeer;
    ASSERT_TRUE(clampedPeer.Connect("scroll-clamp"));
    clampedPeer.Send(R"({"type":"scroll","x":0.5,"y":0.5,"dy":9.0})");
    ASSERT_TRUE(clampedPeer.WaitForTouchEvents(3));
    EXPECT_NEAR(clampedPeer.TouchEvents()[1].normalizedY, 0.75, 0.01);
}

TEST(InputRouter, CoordinatesAreClampedBeforeScrcpyControl) {
    NegotiatedInputPeer peer;
    ASSERT_TRUE(peer.Connect("coordinate-clamp"));

    peer.Send(R"({"type":"click","x":-0.5,"y":1.5})");
    ASSERT_TRUE(peer.WaitForTouchEvents(2));
    for (const auto& event : peer.TouchEvents()) {
        EXPECT_EQ(event.x, 0u);
        EXPECT_EQ(event.y, 200u);
    }
}

TEST(InputRouter, ScrollCancelsHeldDragOnceAndMakesLaterDragEndANoOp) {
    NegotiatedInputPeer peer;
    ASSERT_TRUE(peer.Connect("input-scroll-bounds"));

    peer.Send(R"({"type":"drag_start","x":0.25,"y":0.25})");
    peer.Send(R"({"type":"drag_move","x":0.4,"y":0.4})");
    peer.Send(R"({"type":"scroll","x":0.5,"y":0.5,"dy":0.10})");
    peer.Send(R"({"type":"drag_end","x":0.8,"y":0.8})");

    ASSERT_TRUE(peer.WaitForTouchEvents(6));
    auto events = peer.TouchEvents();
    ASSERT_EQ(events.size(), 6u);
    EXPECT_EQ(peer.TouchActions(), (std::vector<std::uint8_t>{
        ScrcpyControlClient::ACTION_DOWN,
        ScrcpyControlClient::ACTION_MOVE,
        ScrcpyControlClient::ACTION_UP,
        ScrcpyControlClient::ACTION_DOWN,
        ScrcpyControlClient::ACTION_MOVE,
        ScrcpyControlClient::ACTION_UP,
    }));
    EXPECT_EQ(events[2].x, 40u);
    EXPECT_EQ(events[2].y, 80u);
}

TEST(InputRouter, InvalidCoordinateOrScrollDeltaDoesNotTouchScrcpyControl) {
    NegotiatedInputPeer peer;
    ASSERT_TRUE(peer.Connect("invalid-input"));

    peer.Send(R"({"type":"scroll","x":0.5,"y":0.5})");
    peer.Send(R"({"type":"drag_start","x":true,"y":0.5})");
    peer.Send(R"({"type":"drag_move","x":0.5,"y":false})");
    peer.Send(R"({"type":"scroll","x":0.5,"y":0.5,"dy":true})");
    peer.Send(R"({"type":"scroll","x":0.5,"y":0.5,"dy":null})");

    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    EXPECT_EQ(peer.fake.ControlBytesReceived(), 0u);
}

TEST(InputRouter, ClickClearsActiveDragState) {
    NegotiatedInputPeer peer;
    ASSERT_TRUE(peer.Connect("input-click-clears-drag"));

    peer.Send(R"({"type":"drag_start","x":0.1,"y":0.1})");
    peer.Send(R"({"type":"click","x":0.5,"y":0.5})");
    peer.Send(R"({"type":"drag_end","x":0.8,"y":0.8})");

    ASSERT_TRUE(peer.WaitForTouchEvents(3));
    auto events = peer.TouchEvents();
    ASSERT_EQ(events.size(), 3u);
    EXPECT_EQ(events[0].action, ScrcpyControlClient::ACTION_DOWN);
    EXPECT_EQ(events[1].action, ScrcpyControlClient::ACTION_DOWN);
    EXPECT_EQ(events[2].action, ScrcpyControlClient::ACTION_UP);
}
