#pragma once

#include "peer_registry.h"
#include "scrcpy_control.h"
#include "scrcpy_video.h"
#include "source_video_fanout.h"

#include <atomic>
#include <chrono>
#include <cstdint>
#include <memory>
#include <mutex>

enum class SourceHealthState { Connected, Disconnected, Stalled };

struct SourceStatus {
    SourceHealthState state;
    std::uint64_t generation;
    int width;
    int height;
};

class ScrcpySource {
public:
    explicit ScrcpySource(
        PeerRegistry& registry,
        std::chrono::milliseconds stallThreshold = std::chrono::milliseconds(5000));
    ~ScrcpySource();

    ScrcpySource(const ScrcpySource&) = delete;
    ScrcpySource& operator=(const ScrcpySource&) = delete;

    void ConnectInitial(
        int port,
        int maxRetries = 20,
        std::chrono::milliseconds retryDelay = std::chrono::milliseconds(250));
    bool Reconnect(int newPort, std::uint64_t requestedGeneration);
    void RequestIdr();
    SourceStatus Status() const;
    std::shared_ptr<ScrcpyControlClient> Control() const;

private:
    struct PendingConnection;
    struct RetiredConnection;

    PendingConnection PrepareConnection(
        int port,
        int maxRetries,
        std::chrono::milliseconds retryDelay);
    RetiredConnection RetireCurrent();
    void Install(PendingConnection connection, std::uint64_t generation);
    void FanOut(const std::uint8_t* data, size_t size);

    PeerRegistry& registry_;
    std::chrono::milliseconds stallThreshold_;
    H264SourceAccessUnitPreparer accessUnitPreparer_;
    SourceVideoFanout videoFanout_;

    // lifecycleMutex_ serializes connect/reconnect operations. stateMutex_
    // protects only short state swaps, so status/control calls never wait on
    // socket I/O or a reader-thread join.
    mutable std::mutex lifecycleMutex_;
    mutable std::mutex stateMutex_;
    std::unique_ptr<ScrcpyVideoClient> video_;
    std::shared_ptr<ScrcpyControlClient> control_;
    std::uint64_t generation_ = 0;
    int width_ = 0;
    int height_ = 0;
    bool connected_ = false;
    std::atomic<std::chrono::steady_clock::time_point> lastFrameAt_;
};
