#pragma once
#include <cstdint>
#include <cstddef>
#include <functional>
#include <memory>
#include <stdexcept>

class NvencEncoder {
public:
    using NaluCallback = std::function<void(const uint8_t* annexBData, size_t size)>;

    NvencEncoder(int width, int height, int fps, int bitrateKbps);
    ~NvencEncoder();

    NvencEncoder(const NvencEncoder&) = delete;
    NvencEncoder& operator=(const NvencEncoder&) = delete;

    void EncodeFrame(const uint8_t* bgraData, int strideBytes);
    void SetCallback(NaluCallback onNalu);

private:
    struct Impl;
    std::unique_ptr<Impl> impl_;
};
