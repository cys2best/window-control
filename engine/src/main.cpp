// engine/src/main.cpp
#include "scrcpy_video.h"
#include "scrcpy_control.h"
#include "signaling_client.h"
#include "peer.h"
#include <nlohmann/json.hpp>
#include <iostream>
#include <csignal>
#include <atomic>
#include <thread>
#include <chrono>

using json = nlohmann::json;

std::atomic<bool> g_running{true};

void OnSigint(int) { g_running = false; }

int main(int argc, char** argv) {
    if (argc < 5) {
        std::cerr << "Usage: engine.exe <scrcpy_port> <signaling_ws_url> "
                     "<session_id> <stun_turn_url>\n"
                     "Example: engine.exe 27183 ws://VPS_IP:8443 poc-session-1 "
                     "stun:VPS_IP:3478\n"
                     "(scrcpy_port is the localhost port `adb forward` set up "
                     "for a scrcpy-server started manually per this plan's "
                     "Task 9 e2e steps — the Python control-plane wires this "
                     "automatically starting in Phase 3.)\n";
        return 1;
    }

    int scrcpyPort = std::stoi(argv[1]);
    std::string signalingUrl = argv[2];
    std::string sessionId = argv[3];
    std::string stunTurnUrl = argv[4];

    std::signal(SIGINT, OnSigint);

    // scrcpy connect-order: video first (reads dummy byte), then control
    // (unblocks scrcpy-server's accept(), which then sends the video
    // handshake), then read that handshake.
    ScrcpyVideoClient video(scrcpyPort);
    video.Connect();

    ScrcpyControlClient control(scrcpyPort);
    control.Connect();

    video.ReadHandshake();
    std::cout << "scrcpy handshake: device=" << video.DeviceName()
              << " " << video.Width() << "x" << video.Height() << "\n" << std::flush;

    try {
        std::cout << "[debug] constructing SignalingClient..." << std::endl;
        SignalingClient signaling(signalingUrl, sessionId, "engine", /*token=*/"");

        std::cout << "[debug] constructing WebRtcPeer..." << std::endl;
        WebRtcPeer peer(signaling, {stunTurnUrl});

        int screenWidth = video.Width();
        int screenHeight = video.Height();
        peer.SetInputCallback([&control, screenWidth, screenHeight](const std::string& jsonMsg) {
            auto msg = json::parse(jsonMsg, nullptr, false);
            if (msg.is_discarded()) return;

            std::string type = msg.value("type", "");
            if (type == "tap" || type == "swipe") {
                std::string action = msg.value("action", "down");
                uint8_t actionCode = ScrcpyControlClient::ACTION_DOWN;
                if (action == "up") actionCode = ScrcpyControlClient::ACTION_UP;
                else if (action == "move") actionCode = ScrcpyControlClient::ACTION_MOVE;

                double nx = msg.value("nx", 0.0);
                double ny = msg.value("ny", 0.0);
                control.SendTouch(actionCode, nx, ny, screenWidth, screenHeight);
            } else if (type == "key") {
                int keycode = msg.value("keycode", 0);
                if (keycode != 0) control.SendKeycode(keycode);
            }
        });

        // Request a fresh IDR once the connection is actually up (not blindly at
        // startup) so a viewer joining mid-stream doesn't wait for the next
        // scheduled keyframe.
        peer.SetOnConnected([&control]() {
            std::cout << "[debug] peer connected, requesting IDR" << std::endl;
            control.RequestIdr();
        });

        std::cout << "[debug] calling StartAsOfferer..." << std::endl;
        peer.StartAsOfferer();
        std::cout << "[debug] StartAsOfferer returned" << std::endl;

        video.StartReading([&peer](const uint8_t* data, size_t size) {
            peer.SendVideoNalu(data, size);
        });

        std::cout << "Streaming started. Press Ctrl+C to stop.\n" << std::flush;
        while (g_running) {
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }

        // Shutdown order is load-bearing: stop things in the reverse order they
        // were started. video.Stop() must run before peer/signaling teardown so
        // the read thread (which calls peer.SendVideoNalu) is joined first and
        // can't race the peer's destruction; the peer/signaling then close out
        // as WebRtcPeer's destructor runs at scope exit.
        video.Stop();
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
