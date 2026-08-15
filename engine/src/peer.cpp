// engine/src/peer.cpp
#include "peer.h"
#include <rtc/rtc.hpp>
#include <nlohmann/json.hpp>
#include <iostream>

using json = nlohmann::json;

struct WebRtcPeer::Impl {
    SignalingClient& signaling;
    std::shared_ptr<rtc::PeerConnection> pc;
    std::shared_ptr<rtc::Track> videoTrack;
    std::shared_ptr<rtc::RtpPacketizationConfig> rtpConfig;
    std::shared_ptr<rtc::H264RtpPacketizer> packetizer;
    std::shared_ptr<rtc::DataChannel> inputChannel;
    InputCallback onInput;

    explicit Impl(SignalingClient& s) : signaling(s) {}
};

WebRtcPeer::WebRtcPeer(SignalingClient& signaling, const std::vector<std::string>& stunTurnUrls)
    : impl_(std::make_unique<Impl>(signaling)) {

    rtc::Configuration config;
    for (const auto& url : stunTurnUrls) {
        config.iceServers.emplace_back(url);
    }

    impl_->pc = std::make_shared<rtc::PeerConnection>(config);

    impl_->pc->onLocalDescription([this](rtc::Description desc) {
        json msg = {
            {"type", desc.typeString()},
            {"sdp", std::string(desc)}
        };
        impl_->signaling.Send(msg.dump());
    });

    impl_->pc->onLocalCandidate([this](rtc::Candidate cand) {
        json msg = {
            {"type", "candidate"},
            {"candidate", std::string(cand)},
            {"mid", cand.mid()}
        };
        impl_->signaling.Send(msg.dump());
    });

    impl_->pc->onStateChange([](rtc::PeerConnection::State state) {
        std::cout << "[peer] state: " << static_cast<int>(state) << std::endl;
    });

    impl_->signaling.Connect([this](const std::string& jsonMsg) {
        auto msg = json::parse(jsonMsg, nullptr, false);
        if (msg.is_discarded()) return;

        std::string type = msg.value("type", "");
        if (type == "answer") {
            impl_->pc->setRemoteDescription(rtc::Description(msg["sdp"].get<std::string>(), type));
        } else if (type == "candidate") {
            impl_->pc->addRemoteCandidate(rtc::Candidate(
                msg["candidate"].get<std::string>(), msg.value("mid", "")));
        }
    });
}

WebRtcPeer::~WebRtcPeer() = default;

void WebRtcPeer::StartAsOfferer() {
    rtc::Description::Video media("video", rtc::Description::Direction::SendOnly);
    media.addH264Codec(96);
    media.setBitrate(8000);

    impl_->videoTrack = impl_->pc->addTrack(media);

    impl_->rtpConfig = std::make_shared<rtc::RtpPacketizationConfig>(
        /*ssrc=*/1, /*cname=*/"engine-video", /*payloadType=*/96,
        rtc::H264RtpPacketizer::defaultClockRate);
    impl_->packetizer = std::make_shared<rtc::H264RtpPacketizer>(
        rtc::NalUnit::Separator::LongStartSequence, impl_->rtpConfig);

    auto srReporter = std::make_shared<rtc::RtcpSrReporter>(impl_->rtpConfig);
    impl_->packetizer->addToChain(srReporter);
    auto nackResponder = std::make_shared<rtc::RtcpNackResponder>();
    impl_->packetizer->addToChain(nackResponder);
    impl_->videoTrack->setMediaHandler(impl_->packetizer);

    // Input DataChannel — engine is the offerer, so it creates the channel;
    // the browser/mobile client receives it via RTCPeerConnection's
    // ondatachannel event (see Task 9's test_page.html).
    impl_->inputChannel = impl_->pc->createDataChannel("input");
    impl_->inputChannel->onMessage([this](rtc::message_variant data) {
        if (!impl_->onInput) return;
        if (std::holds_alternative<std::string>(data)) {
            impl_->onInput(std::get<std::string>(data));
        }
    });

    impl_->pc->setLocalDescription();
}

void WebRtcPeer::SendVideoNalu(const uint8_t* data, size_t size) {
    if (!impl_->videoTrack || !impl_->videoTrack->isOpen()) return;
    impl_->videoTrack->send(reinterpret_cast<const std::byte*>(data), size);
}

void WebRtcPeer::SetInputCallback(InputCallback onInput) {
    impl_->onInput = std::move(onInput);
}
