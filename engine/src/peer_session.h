#pragma once
#include <rtc/rtc.hpp>
#include <chrono>
#include <functional>
#include <memory>
#include <string>
#include <vector>

class PeerSession {
public:
    using InputCallback = std::function<void(const std::string& jsonMessage)>;
    using StateCallback = std::function<void(rtc::PeerConnection::State)>;

    PeerSession(std::string id, const std::vector<std::string>& iceServers);
    ~PeerSession();

    PeerSession(const PeerSession&) = delete;
    PeerSession& operator=(const PeerSession&) = delete;

    std::string AnswerOffer(const std::string& remoteSdpOffer,
                             std::chrono::milliseconds gatherTimeout =
                                 std::chrono::milliseconds(10000));

    void SendVideoNalu(const uint8_t* data, size_t size);
    void SetInputCallback(InputCallback onInput);
    void SetOnStateChange(StateCallback onStateChange);
    void Close();

    const std::string& Id() const;
    rtc::PeerConnection::State State() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
