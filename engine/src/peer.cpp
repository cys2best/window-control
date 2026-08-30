// engine/src/peer.cpp
#include "peer.h"
#include "h264_nalu.h"
#include <rtc/rtc.hpp>
#include <nlohmann/json.hpp>
#include <iostream>
#include <chrono>

using json = nlohmann::json;

struct WebRtcPeer::Impl {
    SignalingClient& signaling;
    std::shared_ptr<rtc::PeerConnection> pc;
    std::shared_ptr<rtc::Track> videoTrack;
    std::shared_ptr<rtc::RtpPacketizationConfig> rtpConfig;
    std::shared_ptr<rtc::H264RtpPacketizer> packetizer;
    std::shared_ptr<rtc::DataChannel> inputChannel;
    InputCallback onInput;
    std::function<void()> onConnected;
    std::chrono::steady_clock::time_point streamStart;
    h264::SpsPpsCache spsPpsCache;

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
        std::cout << "[debug] onLocalDescription fired, type=" << desc.typeString()
                  << " signaling.IsConnected=" << impl_->signaling.IsConnected() << std::endl;
        std::cout << "[debug] SDP:\n" << std::string(desc) << std::endl;
        impl_->signaling.Send(msg.dump());
    });

    impl_->pc->onLocalCandidate([this](rtc::Candidate cand) {
        json msg = {
            {"type", "candidate"},
            {"candidate", std::string(cand)},
            {"mid", cand.mid()}
        };
        std::cout << "[debug] onLocalCandidate fired" << std::endl;
        impl_->signaling.Send(msg.dump());
    });

    impl_->pc->onStateChange([this](rtc::PeerConnection::State state) {
        std::cout << "[peer] state: " << static_cast<int>(state) << std::endl;
        if (state == rtc::PeerConnection::State::Connected && impl_->onConnected) {
            impl_->onConnected();
        }
    });
}

WebRtcPeer::~WebRtcPeer() {
    // Stop callbacks from firing into a half-destroyed object: close the
    // PeerConnection (which stops onStateChange/onLocalDescription/
    // onLocalCandidate from firing) and disconnect signaling before impl_
    // (and the members its callbacks capture by raw `this`) are torn down.
    if (impl_) {
        if (impl_->pc) impl_->pc->close();
        impl_->signaling.Disconnect();
    }
}

void WebRtcPeer::StartAsOfferer() {
    // Connect signaling here (not in the ctor) so the WS connection and
    // remote-description handling only begin once there's a local
    // description in progress to receive an answer against.
    impl_->signaling.Connect([this](const std::string& jsonMsg) {
        auto msg = json::parse(jsonMsg, nullptr, false);
        if (msg.is_discarded()) return;

        std::string type = msg.value("type", "");
        if (type == "answer") {
            std::string sdp = msg.value("sdp", std::string());
            if (sdp.empty()) return;
            impl_->pc->setRemoteDescription(rtc::Description(sdp, type));
        } else if (type == "candidate") {
            std::string candidate = msg.value("candidate", std::string());
            if (candidate.empty()) return;
            impl_->pc->addRemoteCandidate(rtc::Candidate(candidate, msg.value("mid", "")));
        }
    });

    impl_->streamStart = std::chrono::steady_clock::now();

    rtc::Description::Video media("video", rtc::Description::Direction::SendOnly);
    media.addH264Codec(96);
    media.setBitrate(8000);

    impl_->videoTrack = impl_->pc->addTrack(media);

    impl_->rtpConfig = std::make_shared<rtc::RtpPacketizationConfig>(
        /*ssrc=*/1, /*cname=*/"engine-video", /*payloadType=*/96,
        rtc::H264RtpPacketizer::defaultClockRate);
    impl_->packetizer = std::make_shared<rtc::H264RtpPacketizer>(
        rtc::NalUnit::Separator::StartSequence, impl_->rtpConfig);

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
    static int callCount = 0;
    static int droppedCount = 0;

    // Parameter sets commonly arrive before DTLS opens the media track. The
    // transmission may be dropped below, but the configuration state cannot be.
    auto prepared = impl_->spsPpsCache.ObserveAndPrepare(data, size);

    bool trackOpen = impl_->videoTrack && impl_->videoTrack->isOpen();
    if (callCount < 5 || callCount % 60 == 0) {
        std::cout << "[debug] SendVideoNalu call #" << callCount << " size=" << size
                  << " trackOpen=" << trackOpen << " dropped=" << droppedCount << std::endl;
    }
    ++callCount;
    if (!trackOpen) { ++droppedCount; return; }

    auto elapsed = std::chrono::steady_clock::now() - impl_->streamStart;
    double elapsedSeconds = std::chrono::duration<double>(elapsed).count();
    impl_->rtpConfig->timestamp = impl_->rtpConfig->startTimestamp +
        impl_->rtpConfig->secondsToTimestamp(elapsedSeconds);

    const uint8_t* sendData = data;
    size_t sendSize = size;
    if (prepared.has_value()) {
        sendData = prepared->data();
        sendSize = prepared->size();
    }

    try {
        // One packetizer input represents one H264 access unit. Sending config
        // and IDR separately would create two RTP marker boundaries.
        impl_->videoTrack->send(
            reinterpret_cast<const std::byte*>(sendData), sendSize);
    } catch (const std::exception& e) {
        std::cerr << "[debug] videoTrack->send threw: " << e.what() << std::endl;
        throw;
    }
}

void WebRtcPeer::SetInputCallback(InputCallback onInput) {
    impl_->onInput = std::move(onInput);
}

void WebRtcPeer::SetOnConnected(std::function<void()> onConnected) {
    impl_->onConnected = std::move(onConnected);
}
