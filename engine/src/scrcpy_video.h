// scrcpy_video.h
#pragma once
#include <cstdint>
#include <cstddef>
#include <functional>
#include <memory>
#include <stdexcept>
#include <string>
#include <atomic>
#include <thread>

class ScrcpyVideoClient {
public:
    using NaluCallback = std::function<void(const uint8_t* annexBData, size_t size)>;

    explicit ScrcpyVideoClient(int port);
    ~ScrcpyVideoClient();

    ScrcpyVideoClient(const ScrcpyVideoClient&) = delete;
    ScrcpyVideoClient& operator=(const ScrcpyVideoClient&) = delete;

    void Connect();
    void ReadHandshake();
    void StartReading(NaluCallback onNalu);
    void Stop();

    std::string DeviceName() const;
    int Width() const;
    int Height() const;

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
