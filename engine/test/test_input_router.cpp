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
