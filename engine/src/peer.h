// engine/src/peer.h
#pragma once
#include <rtc/rtc.hpp>
#include "signaling_client.h"
#include <functional>
#include <memory>
#include <vector>
#include <string>

class WebRtcPeer {
public:
    using InputCallback = std::function<void(const std::string& jsonMessage)>;

    WebRtcPeer(SignalingClient& signaling, const std::vector<std::string>& stunTurnUrls);
    ~WebRtcPeer();

    WebRtcPeer(const WebRtcPeer&) = delete;
    WebRtcPeer& operator=(const WebRtcPeer&) = delete;

    void StartAsOfferer();
    void SendVideoNalu(const uint8_t* data, size_t size);
    void SetInputCallback(InputCallback onInput);
    void SetOnConnected(std::function<void()> onConnected);

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
