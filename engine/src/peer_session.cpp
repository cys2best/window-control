#include "peer_session.h"
#include <chrono>
#include <condition_variable>
#include <mutex>
#include <stdexcept>
#include <utility>

namespace {

int SelectOfferedH264PayloadType(const rtc::Description::Media& media) {
    int fallback = -1;
    for (int payloadType : media.payloadTypes()) {
        const auto* rtpMap = media.rtpMap(payloadType);
        if (!rtpMap || rtpMap->format != "H264") continue;
        if (fallback < 0) fallback = payloadType;

        // Prefer non-interleaved mode: the H264 RTP packetizer fragments large
        // NAL units with FU-A, which requires packetization-mode=1.
        for (const auto& parameter : rtpMap->fmtps) {
            if (parameter.find("packetization-mode=1") != std::string::npos) {
                return payloadType;
            }
        }
    }
    return fallback;
}

}  // namespace

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

    std::mutex callbackMutex;
    std::mutex mediaMutex;
    std::string mediaError;
    std::mutex gatherMutex;
    std::condition_variable gatherCv;
    bool gatheringComplete = false;
};

PeerSession::PeerSession(std::string id, const std::vector<std::string>& iceServers)
    : impl_(std::make_unique<Impl>()) {
    impl_->id = std::move(id);

    rtc::Configuration config;
    for (const auto& url : iceServers) config.iceServers.emplace_back(url);
    // PeerSession is always the answerer. Automatic negotiation would create
    // the answer inside setRemoteDescription(), before AnswerOffer can validate
    // and observe the configured track, then the later explicit call would
    // create a second local offer with setup:actpass.
    config.disableAutoNegotiation = true;
    impl_->pc = std::make_shared<rtc::PeerConnection>(config);

    impl_->pc->onGatheringStateChange([this](rtc::PeerConnection::GatheringState state) {
        if (state != rtc::PeerConnection::GatheringState::Complete) return;
        std::lock_guard<std::mutex> lock(impl_->gatherMutex);
        impl_->gatheringComplete = true;
        impl_->gatherCv.notify_all();
    });

    impl_->pc->onStateChange([this](rtc::PeerConnection::State state) {
        std::lock_guard<std::mutex> lock(impl_->callbackMutex);
        if (impl_->onStateChange) impl_->onStateChange(state);
    });

    // The viewer creates "input" (see engine/test/test_peer_session.cpp); this side only
    // observes it arriving.
    impl_->pc->onDataChannel([this](std::shared_ptr<rtc::DataChannel> dc) {
        if (dc->label() != "input") return;
        impl_->inputChannel = dc;
        impl_->inputChannel->onMessage([this](rtc::message_variant data) {
            if (!std::holds_alternative<std::string>(data)) return;
            std::lock_guard<std::mutex> lock(impl_->callbackMutex);
            if (impl_->onInput) impl_->onInput(std::get<std::string>(data));
        });
    });

    // A browser offer owns the media-section order, MID, and payload types.
    // Configure the offered track in place; adding a new hard-coded track while
    // answering produces an SDP that Chromium correctly rejects.
    impl_->pc->onTrack([this](std::shared_ptr<rtc::Track> track) {
        try {
            auto description = track->description();
            if (description.type() != "video") return;

            const int payloadType = SelectOfferedH264PayloadType(description);
            if (payloadType < 0) {
                throw std::runtime_error("browser offer has no H264 video payload type");
            }

            description.setBitrate(8000);
            description.addSSRC(/*ssrc=*/1, /*cname=*/"engine-video");
            track->setDescription(std::move(description));

            auto rtpConfig = std::make_shared<rtc::RtpPacketizationConfig>(
                /*ssrc=*/1, /*cname=*/"engine-video", payloadType,
                rtc::H264RtpPacketizer::defaultClockRate);
            auto packetizer = std::make_shared<rtc::H264RtpPacketizer>(
                rtc::NalUnit::Separator::StartSequence, rtpConfig);
            auto srReporter = std::make_shared<rtc::RtcpSrReporter>(rtpConfig);
            packetizer->addToChain(srReporter);
            auto nackResponder = std::make_shared<rtc::RtcpNackResponder>();
            packetizer->addToChain(nackResponder);
            track->setMediaHandler(packetizer);

            {
                std::lock_guard<std::mutex> lock(impl_->mediaMutex);
                impl_->videoTrack = std::move(track);
                impl_->rtpConfig = std::move(rtpConfig);
                impl_->packetizer = std::move(packetizer);
            }
        } catch (const std::exception& e) {
            std::lock_guard<std::mutex> lock(impl_->mediaMutex);
            impl_->mediaError = e.what();
        }
    });
}

PeerSession::~PeerSession() { Close(); }

std::string PeerSession::AnswerOffer(
    const std::string& remoteSdpOffer, std::chrono::milliseconds gatherTimeout) {
    impl_->streamStart = std::chrono::steady_clock::now();

    try {
        // libdatachannel creates the reciprocal offered tracks while building
        // the local answer. Its synchronous onTrack callback above configures
        // the video track before that media section is serialized, preserving
        // the browser's m-line order, MID, and H264 payload type.
        impl_->pc->setRemoteDescription(rtc::Description(remoteSdpOffer, "offer"));
        impl_->pc->setLocalDescription(rtc::Description::Type::Answer);

        std::string mediaError;
        {
            std::lock_guard<std::mutex> lock(impl_->mediaMutex);
            if (!impl_->mediaError.empty()) {
                mediaError = impl_->mediaError;
            } else if (!impl_->videoTrack) {
                mediaError = "browser offer has no usable video media section";
            }
        }
        if (!mediaError.empty()) throw std::runtime_error(mediaError);
    } catch (const std::exception& e) {
        impl_->pc->close();
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
    std::shared_ptr<rtc::Track> videoTrack;
    std::shared_ptr<rtc::RtpPacketizationConfig> rtpConfig;
    {
        std::lock_guard<std::mutex> lock(impl_->mediaMutex);
        videoTrack = impl_->videoTrack;
        rtpConfig = impl_->rtpConfig;
    }
    if (!videoTrack || !rtpConfig || !videoTrack->isOpen()) return;

    auto elapsed = std::chrono::steady_clock::now() - impl_->streamStart;
    double elapsedSeconds = std::chrono::duration<double>(elapsed).count();
    rtpConfig->timestamp = rtpConfig->startTimestamp +
        rtpConfig->secondsToTimestamp(elapsedSeconds);

    videoTrack->send(reinterpret_cast<const std::byte*>(data), size);
}

void PeerSession::SendInputMessage(const std::string& jsonMessage) {
    if (impl_->inputChannel && impl_->inputChannel->isOpen()) {
        impl_->inputChannel->send(jsonMessage);
    }
}

void PeerSession::SetInputCallback(InputCallback onInput) {
    std::lock_guard<std::mutex> lock(impl_->callbackMutex);
    impl_->onInput = std::move(onInput);
}

void PeerSession::SetOnStateChange(StateCallback onStateChange) {
    std::lock_guard<std::mutex> lock(impl_->callbackMutex);
    impl_->onStateChange = std::move(onStateChange);
}

void PeerSession::ClearCallbacks() {
    std::lock_guard<std::mutex> lock(impl_->callbackMutex);
    impl_->onInput = {};
    impl_->onStateChange = {};
}

void PeerSession::Close() {
    if (impl_ && impl_->pc) impl_->pc->close();
}

const std::string& PeerSession::Id() const { return impl_->id; }

rtc::PeerConnection::State PeerSession::State() const {
    return impl_->pc->state();
}
