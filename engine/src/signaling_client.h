#pragma once
#include <string>
#include <functional>
#include <memory>

class SignalingClient {
public:
    using MessageCallback = std::function<void(const std::string& jsonMessage)>;

    SignalingClient(const std::string& wsUrl, const std::string& sessionId,
                     const std::string& role, const std::string& token);
    ~SignalingClient();

    SignalingClient(const SignalingClient&) = delete;
    SignalingClient& operator=(const SignalingClient&) = delete;

    void Connect(MessageCallback onMessage);
    void Send(const std::string& jsonMessage);
    void Disconnect();
    bool IsConnected() const;

private:
    struct Impl;
    std::shared_ptr<Impl> impl_;
};
