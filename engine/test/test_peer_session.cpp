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
