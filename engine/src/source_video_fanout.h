#pragma once

#include "h264_nalu.h"

#include <cstddef>
#include <cstdint>
#include <functional>
#include <optional>
#include <string>
#include <vector>

class SourceAccessUnitPreparer {
public:
    virtual ~SourceAccessUnitPreparer() = default;
    virtual std::optional<std::vector<std::uint8_t>> ObserveAndPrepare(
        const std::uint8_t* data,
        std::size_t size) = 0;
    virtual void Reset() = 0;
};

class H264SourceAccessUnitPreparer final : public SourceAccessUnitPreparer {
public:
    std::optional<std::vector<std::uint8_t>> ObserveAndPrepare(
        const std::uint8_t* data,
        std::size_t size) override;
    void Reset() override;

private:
    h264::SpsPpsCache cache_;
};

struct SourceVideoPeerTarget {
    std::string id;
    std::function<void(const std::uint8_t*, std::size_t)> send;
    std::function<void()> markFailed = {};
};

// Owns the per-access-unit ordering contract: observe the source-global
// codec state once, snapshot peers afterward, and isolate each delivery.
class SourceVideoFanout {
public:
    using SnapshotProvider = std::function<std::vector<SourceVideoPeerTarget>()>;

    SourceVideoFanout(
        SourceAccessUnitPreparer& preparer,
        SnapshotProvider snapshotProvider);

    void BeginGeneration();
    void SendAccessUnit(const std::uint8_t* data, std::size_t size);

private:
    void MarkFailedPeer(const SourceVideoPeerTarget& peer);

    SourceAccessUnitPreparer& preparer_;
    SnapshotProvider snapshotProvider_;
};
