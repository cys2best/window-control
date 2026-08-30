#include "public_signaling.h"
#include <iostream>

PublicSignalingBridge::PublicSignalingBridge(
    SignalingClient& signaling, PeerRegistry& registry, std::vector<std::string> iceServers)
    : signaling_(signaling), registry_(registry), iceServers_(std::move(iceServers)) {}

void PublicSignalingBridge::Start() {
    signaling_.Connect([this](const std::string& rawSdpOffer) {
        std::string id = "public-" + std::to_string(++nextPublicSeq_);
        auto session = registry_.Create(PeerKind::Public, id, iceServers_);
        if (!session) {
            std::cerr << "[public_signaling] failed to create public peer slot" << std::endl;
            return;
        }
        try {
            std::string answer = session->AnswerOffer(rawSdpOffer);
            signaling_.Send(answer);
        } catch (const std::exception& e) {
            std::cerr << "[public_signaling] AnswerOffer failed, dropping: " << e.what() << std::endl;
            registry_.Remove(id);
        }
    });
}
