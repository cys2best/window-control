#include "peer_session.h"
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <stdexcept>

struct PeerSession::Impl {
    std::string id;
    std::shared_ptr<rtc::PeerConnection> pc;
    std::shared_ptr<rtc::Track> videoTrack;
    std::shared_ptr<rtc::RtpPacketizationConfig> rtpConfig;
    std::shared_ptr<rtc::H264RtpPacketizer> packetizer;
    std::shared_ptr<rtc::DataChannel> inputChannel;
    InputCallback onInput;
    StateCallback onStateChange;
    std::chrono::steady_clock::time_point streamStart;

    std::mutex gatherMutex;
    std::condition_variable gatherCv;
    bool gatheringComplete = false;
};

PeerSession::PeerSession(std::string id, const std::vector<std::string>& iceServers)
    : impl_(std::make_unique<Impl>()) {
    impl_->id = std::move(id);

    rtc::Configuration config;
    for (const auto& url : iceServers) config.iceServers.emplace_back(url);
    impl_->pc = std::make_shared<rtc::PeerConnection>(config);

    impl_->pc->onGatheringStateChange([this](rtc::PeerConnection::GatheringState state) {
        if (state != rtc::PeerConnection::GatheringState::Complete) return;
        std::lock_guard<std::mutex> lock(impl_->gatherMutex);
        impl_->gatheringComplete = true;
        impl_->gatherCv.notify_all();
    });

    impl_->pc->onStateChange([this](rtc::PeerConnection::State state) {
        if (impl_->onStateChange) impl_->onStateChange(state);
    });

    // The viewer creates "input" (see engine/test/test_peer_session.cpp); this side only
    // observes it arriving.
    impl_->pc->onDataChannel([this](std::shared_ptr<rtc::DataChannel> dc) {
        if (dc->label() != "input") return;
        impl_->inputChannel = dc;
        impl_->inputChannel->onMessage([this](rtc::message_variant data) {
            if (!impl_->onInput) return;
            if (std::holds_alternative<std::string>(data)) {
                impl_->onInput(std::get<std::string>(data));
            }
        });
    });
}

PeerSession::~PeerSession() { Close(); }

std::string PeerSession::AnswerOffer(
    const std::string& remoteSdpOffer, std::chrono::milliseconds gatherTimeout) {
    impl_->streamStart = std::chrono::steady_clock::now();

    try {
        // setRemoteDescription must be called before addTrack/setLocalDescription
        // so that libdatachannel generates an answer SDP (matching the offer) rather
        // than creating a fresh offer itself.
        impl_->pc->setRemoteDescription(rtc::Description(remoteSdpOffer, "offer"));

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

        impl_->pc->setLocalDescription();
    } catch (const std::exception& e) {
        throw std::runtime_error(std::string("PeerSession: failed to answer offer: ") + e.what());
    }

    std::unique_lock<std::mutex> lock(impl_->gatherMutex);
    bool ok = impl_->gatherCv.wait_for(lock, gatherTimeout,
        [this] { return impl_->gatheringComplete; });
    if (!ok) {
        impl_->pc->close();
        throw std::runtime_error("PeerSession: ICE gathering timed out");
    }

    return std::string(*impl_->pc->localDescription());
}

void PeerSession::SendVideoNalu(const uint8_t* data, size_t size) {
    if (!impl_->videoTrack || !impl_->videoTrack->isOpen()) return;

    auto elapsed = std::chrono::steady_clock::now() - impl_->streamStart;
    double elapsedSeconds = std::chrono::duration<double>(elapsed).count();
    impl_->rtpConfig->timestamp = impl_->rtpConfig->startTimestamp +
        impl_->rtpConfig->secondsToTimestamp(elapsedSeconds);

    impl_->videoTrack->send(reinterpret_cast<const std::byte*>(data), size);
}

void PeerSession::SendInputMessage(const std::string& jsonMessage) {
    if (impl_->inputChannel && impl_->inputChannel->isOpen()) {
        impl_->inputChannel->send(jsonMessage);
    }
}

void PeerSession::SetInputCallback(InputCallback onInput) {
    impl_->onInput = std::move(onInput);
}

void PeerSession::SetOnStateChange(StateCallback onStateChange) {
    impl_->onStateChange = std::move(onStateChange);
}

void PeerSession::Close() {
    if (impl_ && impl_->pc) impl_->pc->close();
}

const std::string& PeerSession::Id() const { return impl_->id; }

rtc::PeerConnection::State PeerSession::State() const {
    return impl_->pc->state();
}
