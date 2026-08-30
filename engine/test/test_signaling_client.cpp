#include <gtest/gtest.h>
#include "signaling_client.h"
#include "signaling_test_utils.h"
#include <thread>
#include <chrono>
#include <atomic>
#include <stdexcept>

// Assumes a signaling server instance is already running at
// ws://localhost:8443 with JWT auth disabled (JWT_SECRET unset) — see
// test/README.md for how to start one for local test runs.

TEST(SignalingClient, ConnectsAndExchangesMessages) {
    const auto session = signaling_test::UniqueSession("exchange");
    SignalingClient engineSide("ws://localhost:8443", session, "engine", "");
    SignalingClient viewerSide("ws://localhost:8443", session, "viewer", "");

    std::atomic<bool> received{false};
    std::string receivedMsg;

    viewerSide.Connect([&](const std::string& msg) {
        receivedMsg = msg;
        received = true;
    });
    engineSide.Connect([](const std::string&) {});

    ASSERT_TRUE(signaling_test::WaitUntil([&]() {
        return engineSide.IsConnected() && viewerSide.IsConnected();
    }, std::chrono::seconds(5))) << "session=" << session;

    engineSide.Send(R"({"type":"offer","sdp":"test-sdp-content"})");

    ASSERT_TRUE(signaling_test::WaitUntil(
        [&]() { return received.load(); }, std::chrono::seconds(5)))
        << "session=" << session;
    EXPECT_NE(receivedMsg.find("test-sdp-content"), std::string::npos)
        << "session=" << session << " payload=" << receivedMsg;
}

// Regression test for the offer-lost-to-a-connect-race bug: WebRtcPeer calls
// Send() the instant setLocalDescription() produces an offer, which fires
// essentially synchronously — long before the WS handshake to a remote
// server completes on SignalingClient's ioThread. A Send() that silently
// drops on a not-yet-open connection loses the offer for good.
TEST(SignalingClient, SendImmediatelyAfterConnectIsNotLost) {
    const auto session = signaling_test::UniqueSession("connect-race");
    SignalingClient engineSide("ws://localhost:8443", session, "engine", "");
    SignalingClient viewerSide("ws://localhost:8443", session, "viewer", "");

    std::atomic<bool> received{false};
    std::string receivedMsg;

    viewerSide.Connect([&](const std::string& msg) {
        receivedMsg = msg;
        received = true;
    });
    ASSERT_TRUE(signaling_test::WaitUntil(
        [&]() { return viewerSide.IsConnected(); }, std::chrono::seconds(5)))
        << "session=" << session;

    engineSide.Connect([](const std::string&) {});
    // No settling sleep here — Send() races Connect()'s WS handshake on
    // purpose, matching how WebRtcPeer actually calls it in StartAsOfferer().
    engineSide.Send(R"({"type":"offer","sdp":"race-condition-sdp"})");

    ASSERT_TRUE(signaling_test::WaitUntil(
        [&]() { return received.load(); }, std::chrono::seconds(5)))
        << "session=" << session;
    EXPECT_NE(receivedMsg.find("race-condition-sdp"), std::string::npos)
        << "session=" << session << " payload=" << receivedMsg;
}

TEST(SignalingClient, DisconnectWaitsForCallbackAndSuppressesLaterMessages) {
    const auto session = signaling_test::UniqueSession("disconnect");
    SignalingClient engineSide("ws://localhost:8443", session, "engine", "");
    SignalingClient viewerSide("ws://localhost:8443", session, "viewer", "");

    std::atomic<int> callbackCount{0};
    std::atomic<bool> callbackEntered{false};
    std::atomic<bool> releaseCallback{false};
    engineSide.Connect([&](const std::string&) {
        ++callbackCount;
        callbackEntered = true;
        while (!releaseCallback.load()) std::this_thread::yield();
    });
    viewerSide.Connect([](const std::string&) {});
    ASSERT_TRUE(signaling_test::WaitUntil([&]() {
        return engineSide.IsConnected() && viewerSide.IsConnected();
    }, std::chrono::seconds(5))) << "session=" << session;

    viewerSide.Send("hold-callback");
    const bool entered = signaling_test::WaitUntil(
        [&]() { return callbackEntered.load(); }, std::chrono::seconds(5));
    if (!entered) releaseCallback = true;
    ASSERT_TRUE(entered) << "session=" << session;

    std::atomic<bool> disconnectReturned{false};
    std::thread disconnectThread([&]() {
        engineSide.Disconnect();
        disconnectReturned = true;
    });
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    EXPECT_FALSE(disconnectReturned);

    releaseCallback = true;
    disconnectThread.join();
    EXPECT_TRUE(disconnectReturned);
    EXPECT_FALSE(engineSide.IsConnected());

    engineSide.Disconnect();
    viewerSide.Send("after-disconnect");
    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    EXPECT_EQ(callbackCount.load(), 1);
}

TEST(SignalingClient, DisconnectBeforeConnectIsIdempotent) {
    SignalingClient client("ws://127.0.0.1:1", "unused", "engine", "");

    EXPECT_NO_THROW(client.Disconnect());
    EXPECT_NO_THROW(client.Disconnect());
    EXPECT_FALSE(client.IsConnected());
    EXPECT_THROW(client.Connect([](const std::string&) {}), std::logic_error);
}

TEST(SignalingClient, DisconnectImmediatelyAfterConnectQuiescesHandshake) {
    SignalingClient client("ws://127.0.0.1:1", "connecting", "engine", "");
    std::atomic<int> callbackCount{0};

    client.Connect([&](const std::string&) { ++callbackCount; });
    client.Disconnect();
    client.Disconnect();

    EXPECT_FALSE(client.IsConnected());
    EXPECT_EQ(callbackCount.load(), 0);
}
