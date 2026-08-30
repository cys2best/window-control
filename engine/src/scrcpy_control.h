// engine/src/scrcpy_control.h
#pragma once
#include <cstdint>
#include <memory>

class ScrcpyControlClient {
public:
    static constexpr uint8_t ACTION_DOWN = 0;
    static constexpr uint8_t ACTION_UP   = 1;
    static constexpr uint8_t ACTION_MOVE = 2;

    explicit ScrcpyControlClient(int port);
    ~ScrcpyControlClient();

    ScrcpyControlClient(const ScrcpyControlClient&) = delete;
    ScrcpyControlClient& operator=(const ScrcpyControlClient&) = delete;

    void Connect();
    void SendTouch(uint8_t action, double nx, double ny, int screenWidth, int screenHeight, uint64_t pointerId = 0);
    void SendKeycode(int32_t keycode);
    void RequestIdr();
    bool IsConnected() const;
    bool LastSendFailed() const;
    void ResetSendFailureFlag();

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
