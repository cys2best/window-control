#pragma once
#include "peer_registry.h"
#include "signaling_client.h"
#include <string>
#include <vector>

class PublicSignalingBridge {
public:
    PublicSignalingBridge(SignalingClient& signaling, PeerRegistry& registry,
                           std::vector<std::string> iceServers);
    void Start();

private:
    SignalingClient& signaling_;
    PeerRegistry& registry_;
    std::vector<std::string> iceServers_;
    int nextPublicSeq_ = 0;
};
