#include <gtest/gtest.h>
#include "public_signaling.h"
#include "input_router.h"
#include "peer_registry.h"
#include "scrcpy_source.h"
#include "signaling_client.h"
#include "signaling_test_utils.h"
#include "fake_scrcpy_server.h"
#include <rtc/rtc.hpp>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <iostream>
#include <mutex>
#include <streambuf>
#include <string>
#include <thread>

namespace {
class ThreadSafeDiagnosticCapture final : public std::streambuf {
public:
    explicit ThreadSafeDiagnosticCapture(std::ostream& stream)
        : stream_(stream), original_(stream.rdbuf()) {
        stream_.rdbuf(this);
    }

    ~ThreadSafeDiagnosticCapture() override {
        stream_.rdbuf(original_);
    }

    bool WaitFor(
        const std::string& text,
        std::chrono::milliseconds timeout) {
        std::unique_lock<std::mutex> lock(mutex_);
        return changed_.wait_for(lock, timeout, [&]() {
            return captured_.find(text) != std::string::npos;
        });
    }

protected:
    int_type overflow(int_type character) override {
        if (traits_type::eq_int_type(character, traits_type::eof())) {
            return traits_type::not_eof(character);
        }

        const char value = traits_type::to_char_type(character);
        {
            std::lock_guard<std::mutex> lock(mutex_);
            captured_.push_back(value);
        }
        changed_.notify_all();
        return original_->sputc(value);
    }

    std::streamsize xsputn(
        const char* text,
        std::streamsize count) override {
        {
            std::lock_guard<std::mutex> lock(mutex_);
            captured_.append(text, static_cast<std::size_t>(count));
        }
        changed_.notify_all();
        return original_->sputn(text, count);
    }

    int sync() override {
        return original_->pubsync();
    }

private:
    std::ostream& stream_;
    std::streambuf* original_;
    std::mutex mutex_;
    std::condition_variable changed_;
    std::string captured_;
};

class SignalingDisconnectGuard final {
public:
    SignalingDisconnectGuard(
        SignalingClient& viewerSide,
        SignalingClient& engineSide)
        : viewerSide_(viewerSide), engineSide_(engineSide) {}

    ~SignalingDisconnectGuard() {
        viewerSide_.Disconnect();
        engineSide_.Disconnect();
    }

private:
    SignalingClient& viewerSide_;
    SignalingClient& engineSide_;
};

std::string GatheredOffer() {
    rtc::Configuration config;
    config.disableAutoNegotiation = true;
    rtc::PeerConnection pc(config);
    rtc::Description::Video video(
        "video", rtc::Description::Direction::RecvOnly);
    video.addH264Codec(96);
    auto videoTrack = pc.addTrack(video);
    auto inputChannel = pc.createDataChannel("input");
    std::atomic<bool> gathered{false};
    pc.onGatheringStateChange([&](rtc::PeerConnection::GatheringState s) {
        if (s == rtc::PeerConnection::GatheringState::Complete) gathered = true;
    });
    pc.setLocalDescription();
    for (int i = 0; i < 200 && !gathered; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(25));
    EXPECT_TRUE(videoTrack);
    EXPECT_TRUE(inputChannel);
    return std::string(*pc.localDescription());
}
}

TEST(PublicSignalingBridge, AnswersRawSdpOfferWithRawSdpAnswer) {
    const auto session = signaling_test::UniqueSession("public-answer");
    SignalingClient engineSide("ws://localhost:8443", session, "engine", "");
    SignalingClient viewerSide("ws://localhost:8443", session, "viewer", "");

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

    ASSERT_TRUE(signaling_test::WaitUntil([&]() {
        return engineSide.IsConnected() && viewerSide.IsConnected();
    }, std::chrono::seconds(5))) << "session=" << session;

    viewerSide.Send(GatheredOffer());

    ASSERT_TRUE(signaling_test::WaitUntil(
        [&]() { return gotAnswer.load(); }, std::chrono::seconds(10)))
        << "session=" << session;
    EXPECT_NE(answerSdp.find("v=0"), std::string::npos);
    EXPECT_EQ(answerSdp.find('{'), std::string::npos); // raw SDP, not JSON-wrapped
    EXPECT_TRUE(registry.HasPublicPeer());

    fake.Stop();
}

TEST(PublicSignalingBridge, SecondOfferReplacesFirstPublicPeer) {
    const auto session = signaling_test::UniqueSession("public-replace");
    SignalingClient engineSide("ws://localhost:8443", session, "engine", "");
    SignalingClient viewerSide("ws://localhost:8443", session, "viewer", "");

    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());
    InputRouter inputRouter(source);

    PublicSignalingBridge bridge(engineSide, registry, {}, inputRouter);
    bridge.Start();

    std::atomic<int> answerCount{0};
    viewerSide.Connect([&](const std::string&) { ++answerCount; });
    ASSERT_TRUE(signaling_test::WaitUntil([&]() {
        return engineSide.IsConnected() && viewerSide.IsConnected();
    }, std::chrono::seconds(5))) << "session=" << session;

    viewerSide.Send(GatheredOffer());
    ASSERT_TRUE(signaling_test::WaitUntil(
        [&]() { return answerCount.load() >= 1; }, std::chrono::seconds(10)))
        << "session=" << session;
    ASSERT_EQ(answerCount.load(), 1);
    ASSERT_TRUE(registry.HasPublicPeer());

    viewerSide.Send(GatheredOffer());
    ASSERT_TRUE(signaling_test::WaitUntil(
        [&]() { return answerCount.load() >= 2; }, std::chrono::seconds(10)))
        << "session=" << session;
    EXPECT_EQ(answerCount.load(), 2);
    EXPECT_TRUE(registry.HasPublicPeer());

    fake.Stop();
}

TEST(PublicSignalingBridge, MalformedOfferPreservesExistingPublicPeer) {
    std::atomic<int> answerCount{0};
    const auto session = signaling_test::UniqueSession("public-malformed");
    SignalingClient engineSide("ws://localhost:8443", session, "engine", "");
    SignalingClient viewerSide("ws://localhost:8443", session, "viewer", "");
    ThreadSafeDiagnosticCapture diagnostic(std::cerr);

    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());
    InputRouter inputRouter(source);

    PublicSignalingBridge bridge(engineSide, registry, {}, inputRouter);
    bridge.Start();
    SignalingDisconnectGuard disconnectGuard(viewerSide, engineSide);

    viewerSide.Connect([&](const std::string&) { ++answerCount; });
    ASSERT_TRUE(signaling_test::WaitUntil([&]() {
        return engineSide.IsConnected() && viewerSide.IsConnected();
    }, std::chrono::seconds(5))) << "session=" << session;

    viewerSide.Send(GatheredOffer());
    ASSERT_TRUE(signaling_test::WaitUntil(
        [&]() { return answerCount.load() >= 1; }, std::chrono::seconds(10)))
        << "session=" << session;
    ASSERT_EQ(answerCount.load(), 1);
    auto existing = registry.Find("public-1");
    ASSERT_NE(existing, nullptr);

    viewerSide.Send("not-an-sdp-offer");
    ASSERT_TRUE(diagnostic.WaitFor(
        "[public_signaling] AnswerOffer failed, dropping:",
        std::chrono::seconds(10)));
    viewerSide.Disconnect();
    engineSide.Disconnect();

    EXPECT_EQ(registry.Find("public-1"), existing);
    EXPECT_EQ(answerCount.load(), 1);

    fake.Stop();
}
