#include <gtest/gtest.h>
#include "public_signaling.h"
#include "input_router.h"
#include "peer_registry.h"
#include "scrcpy_source.h"
#include "signaling_client.h"
#include "fake_scrcpy_server.h"
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

    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());
    InputRouter inputRouter(source);

    PublicSignalingBridge bridge(engineSide, registry, {}, inputRouter);
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

    fake.Stop();
}

TEST(PublicSignalingBridge, SecondOfferReplacesFirstPublicPeer) {
    SignalingClient engineSide("ws://localhost:8443", "test-public-2", "engine", "");
    SignalingClient viewerSide("ws://localhost:8443", "test-public-2", "viewer", "");

    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());
    InputRouter inputRouter(source);

    PublicSignalingBridge bridge(engineSide, registry, {}, inputRouter);
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

    fake.Stop();
}
