#include <gtest/gtest.h>
#include "signaling_client.h"
#include <thread>
#include <chrono>
#include <atomic>

// Assumes a signaling server instance is already running at
// ws://localhost:8443 with JWT auth disabled (JWT_SECRET unset) — see
// test/README.md for how to start one for local test runs.

TEST(SignalingClient, ConnectsAndExchangesMessages) {
    SignalingClient engineSide("ws://localhost:8443", "test-session-1", "engine", "");
    SignalingClient viewerSide("ws://localhost:8443", "test-session-1", "viewer", "");

    std::atomic<bool> received{false};
    std::string receivedMsg;

    viewerSide.Connect([&](const std::string& msg) {
        receivedMsg = msg;
        received = true;
    });
    engineSide.Connect([](const std::string&) {});

    std::this_thread::sleep_for(std::chrono::milliseconds(200));
    EXPECT_TRUE(engineSide.IsConnected());
    EXPECT_TRUE(viewerSide.IsConnected());

    engineSide.Send(R"({"type":"offer","sdp":"test-sdp-content"})");

    for (int i = 0; i < 50 && !received; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }

    EXPECT_TRUE(received);
    EXPECT_NE(receivedMsg.find("test-sdp-content"), std::string::npos);
}
