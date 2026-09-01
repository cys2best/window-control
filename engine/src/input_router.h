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
        // Stored coordinates are finite normalized values clamped to [0, 1].
        double x = 0.0;
        double y = 0.0;
    };

    void HandleMessage(PeerSession* peer, const std::string& jsonMessage);
    std::int32_t KeycodeForKey(const std::string& key) const;

    ScrcpySource& source_;
    std::chrono::milliseconds idrRateLimit_;
    std::chrono::steady_clock::time_point lastIdrRequest_{};
    std::mutex idrMutex_;
    std::mutex fingerMutex_;
    std::map<PeerSession*, FingerState> fingerStates_;
};
