#include <gtest/gtest.h>
#include "peer_session.h"
#include <rtc/rtc.hpp>
#include <atomic>
#include <chrono>
#include <sstream>
#include <string>
#include <thread>
#include <vector>

namespace {

struct OfferChannels {
    std::shared_ptr<rtc::Track> video;
    std::shared_ptr<rtc::DataChannel> input;
};

std::shared_ptr<rtc::Track> AddRecvOnlyH264Video(
    rtc::PeerConnection& pc,
    const std::string& mid = "video",
    int payloadType = 96) {
    rtc::Description::Video video(mid, rtc::Description::Direction::RecvOnly);
    video.addH264Codec(payloadType);
    return pc.addTrack(video);
}

// Drives a bare rtc::PeerConnection through the *offerer* side, waiting
// for its own ICE gathering to complete before returning the offer SDP —
// mirrors what a real browser/mobile WHEP or VPS-signaling client does.
std::string CreateGatheredOffer(rtc::PeerConnection& pc, OfferChannels& channels) {
    channels.video = AddRecvOnlyH264Video(pc);
    channels.input = pc.createDataChannel("input");

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

std::string CreateBrowserShapedOffer(
    rtc::PeerConnection& pc,
    OfferChannels& channels) {
    // Chrome commonly assigns PT 96 to VP8 and a later dynamic PT to H264.
    // Its numeric MIDs must be preserved, in order, by the answer.
    rtc::Description::Video video("0", rtc::Description::Direction::RecvOnly);
    video.addVP8Codec(96);
    video.addH264Codec(103);
    channels.video = pc.addTrack(video);
    channels.input = pc.createDataChannel("input");

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

std::vector<std::string> MediaSectionOrder(const std::string& sdp) {
    std::vector<std::string> sections;
    std::istringstream lines(sdp);
    std::string line;
    std::string media;
    while (std::getline(lines, line)) {
        if (!line.empty() && line.back() == '\r') line.pop_back();
        if (line.rfind("m=", 0) == 0) {
            const auto end = line.find(' ', 2);
            media = line.substr(2, end - 2);
        } else if (!media.empty() && line.rfind("a=mid:", 0) == 0) {
            sections.push_back(media + ":" + line.substr(6));
            media.clear();
        }
    }
    return sections;
}

} // namespace

TEST(PeerSession, BrowserOfferPreservesMLineOrderAndOfferedH264PayloadType) {
    rtc::PeerConnection viewerPc;
    OfferChannels channels;
    const std::string offer = CreateBrowserShapedOffer(viewerPc, channels);
    ASSERT_EQ(MediaSectionOrder(offer),
              (std::vector<std::string>{"video:0", "application:1"}));

    PeerSession session("browser-shaped-offer", {});
    const std::string answer = session.AnswerOffer(offer);

    EXPECT_EQ(MediaSectionOrder(answer), MediaSectionOrder(offer));
    EXPECT_NE(answer.find("a=rtpmap:96 VP8/90000"), std::string::npos);
    EXPECT_NE(answer.find("a=rtpmap:103 H264/90000"), std::string::npos);
    EXPECT_NO_THROW(
        viewerPc.setRemoteDescription(rtc::Description(answer, "answer")));

    session.Close();
}

TEST(PeerSession, RejectsBrowserOfferWithoutH264) {
    rtc::PeerConnection viewerPc;
    rtc::Description::Video video("0", rtc::Description::Direction::RecvOnly);
    video.addVP8Codec(96);
    auto videoTrack = viewerPc.addTrack(video);
    auto inputChannel = viewerPc.createDataChannel("input");

    std::atomic<bool> gathered{false};
    viewerPc.onGatheringStateChange([&](rtc::PeerConnection::GatheringState state) {
        if (state == rtc::PeerConnection::GatheringState::Complete) gathered = true;
    });
    viewerPc.setLocalDescription();
    for (int i = 0; i < 200 && !gathered; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(25));
    }

    PeerSession session("vp8-only-offer", {});
    EXPECT_THROW(
        session.AnswerOffer(std::string(*viewerPc.localDescription())),
        std::runtime_error);
}

TEST(PeerSession, AnswersOfferAndReachesConnected) {
    rtc::Configuration viewerConfig;
    rtc::PeerConnection viewerPc(viewerConfig);
    OfferChannels channels;
    std::string offer = CreateGatheredOffer(viewerPc, channels);

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

    // Keep the returned track alive through negotiation: v0.22.4 stores it
    // weakly and marks an expired offered media section as removed.
    auto videoTrack = AddRecvOnlyH264Video(viewerPc);
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

TEST(PeerSession, ConcurrentInputCallbackReplacementAndDispatchRemainSafe) {
    rtc::PeerConnection viewerPc;
    auto inputChannel = viewerPc.createDataChannel("input");
    auto videoTrack = AddRecvOnlyH264Video(viewerPc);
    std::atomic<bool> gathered{false};
    viewerPc.onGatheringStateChange([&](rtc::PeerConnection::GatheringState state) {
        if (state == rtc::PeerConnection::GatheringState::Complete) gathered = true;
    });
    viewerPc.setLocalDescription();
    for (int i = 0; i < 200 && !gathered; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(25));
    }
    std::string offer(*viewerPc.localDescription());

    PeerSession session("test-peer-callback-race", {});
    std::atomic<int> delivered{0};
    session.SetInputCallback([&](const std::string&) { ++delivered; });

    std::string answer = session.AnswerOffer(offer);
    viewerPc.setRemoteDescription(rtc::Description(answer, "answer"));

    std::atomic<bool> dcOpen{false};
    inputChannel->onOpen([&]() { dcOpen = true; });
    for (int i = 0; i < 200 && !dcOpen; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(25));
    }
    ASSERT_TRUE(dcOpen);

    std::thread replacer([&]() {
        for (int i = 0; i < 2000; ++i) {
            session.SetInputCallback([&](const std::string&) { ++delivered; });
        }
    });
    for (int i = 0; i < 2000; ++i) {
        inputChannel->send(std::string(R"({"type":"echo","t":1})"));
    }
    replacer.join();

    std::atomic<bool> gotSentinel{false};
    session.SetInputCallback([&](const std::string& message) {
        if (message == "sentinel") gotSentinel = true;
    });
    inputChannel->send(std::string("sentinel"));
    for (int i = 0; i < 200 && !gotSentinel; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(25));
    }

    EXPECT_TRUE(gotSentinel);
    session.Close();
}
