#include "public_signaling.h"
#include <iostream>

PublicSignalingBridge::PublicSignalingBridge(
    SignalingClient& signaling, PeerRegistry& registry, std::vector<std::string> iceServers,
    InputRouter& inputRouter)
    : signaling_(signaling), registry_(registry), iceServers_(std::move(iceServers)),
      inputRouter_(inputRouter) {}

void PublicSignalingBridge::Start() {
    signaling_.Connect([this](const std::string& rawSdpOffer) {
        std::string id = "public-" + std::to_string(++nextPublicSeq_);
        auto session = std::make_shared<PeerSession>(id, iceServers_);
        inputRouter_.AttachToPeer(*session);
        std::string answer;
        try {
            answer = session->AnswerOffer(rawSdpOffer);
        } catch (const std::exception& e) {
            std::cerr << "[public_signaling] AnswerOffer failed, dropping: " << e.what() << std::endl;
            return;
        }
        if (!registry_.Adopt(PeerKind::Public, id, session)) {
            std::cerr << "[public_signaling] failed to adopt public peer id=" << id << std::endl;
            return;
        }
        signaling_.Send(answer);
        std::cerr << "[public_signaling] public peer ready id=" << id << std::endl;
    });
}
