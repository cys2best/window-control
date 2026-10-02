#include "scrcpy_source.h"

#include <exception>
#include <latch>
#include <stdexcept>
#include <thread>
#include <utility>

struct ScrcpySource::PendingConnection {
    std::unique_ptr<ScrcpyVideoClient> video;
    std::shared_ptr<ScrcpyControlClient> control;
    std::shared_ptr<std::latch> callbackGate;
    int width = 0;
    int height = 0;
};

struct ScrcpySource::RetiredConnection {
    std::unique_ptr<ScrcpyVideoClient> video;
    std::shared_ptr<ScrcpyControlClient> control;
};

ScrcpySource::ScrcpySource(
    PeerRegistry& registry,
    std::chrono::milliseconds stallThreshold)
    : registry_(registry),
      stallThreshold_(stallThreshold),
      videoFanout_(
          accessUnitPreparer_,
          [this]() {
              std::vector<SourceVideoPeerTarget> targets;
              for (const auto& peer : registry_.Snapshot()) {
                  targets.push_back(SourceVideoPeerTarget{
                      peer->Id(),
                      [peer](const std::uint8_t* data, std::size_t size) {
                          peer->SendVideoNalu(data, size);
                      },
                      [this, peer]() {
                          registry_.MarkFailed(peer->Id(), peer);
                      },
                  });
              }
              return targets;
          }),
      lastFrameAt_(std::chrono::steady_clock::now()) {}

ScrcpySource::~ScrcpySource() {
    std::lock_guard<std::mutex> lifecycleLock(lifecycleMutex_);
    auto retired = RetireCurrent();
    if (retired.control) retired.control->Disconnect();
    if (retired.video) retired.video->Stop();
}

ScrcpySource::PendingConnection ScrcpySource::PrepareConnection(
    int port,
    int maxRetries,
    std::chrono::milliseconds retryDelay) {
    if (maxRetries <= 0) {
        throw std::invalid_argument("ScrcpySource: maxRetries must be positive");
    }

    std::unique_ptr<ScrcpyVideoClient> video;
    std::exception_ptr lastError;
    for (int attempt = 0; attempt < maxRetries; ++attempt) {
        auto candidate = std::make_unique<ScrcpyVideoClient>(port);
        try {
            candidate->Connect();
            video = std::move(candidate);
            break;
        } catch (...) {
            lastError = std::current_exception();
            if (attempt + 1 < maxRetries) std::this_thread::sleep_for(retryDelay);
        }
    }
    if (!video) std::rethrow_exception(lastError);

    // scrcpy-server accepts video first, emits its dummy byte, accepts
    // control second, and only then emits video metadata.
    auto control = std::make_shared<ScrcpyControlClient>(port);
    control->Connect();
    control->ResetSendFailureFlag();
    video->ReadHandshake();

    auto callbackGate = std::make_shared<std::latch>(1);
    video->StartReading([this, callbackGate](const std::uint8_t* data, size_t size) {
        // No access unit may race cache reset or fan out before the complete
        // generation is atomically installed.
        callbackGate->wait();
        FanOut(data, size);
    });

    int width = video->Width();
    int height = video->Height();

    return PendingConnection{
        std::move(video),
        std::move(control),
        std::move(callbackGate),
        width,
        height,
    };
}

ScrcpySource::RetiredConnection ScrcpySource::RetireCurrent() {
    std::lock_guard<std::mutex> stateLock(stateMutex_);
    connected_ = false;
    return RetiredConnection{std::move(video_), std::move(control_)};
}

void ScrcpySource::Install(PendingConnection connection, std::uint64_t generation) {
    auto callbackGate = connection.callbackGate;
    {
        std::lock_guard<std::mutex> stateLock(stateMutex_);
        videoFanout_.BeginGeneration();
        video_ = std::move(connection.video);
        control_ = std::move(connection.control);
        width_ = connection.width;
        height_ = connection.height;
        generation_ = generation;
        lastFrameAt_.store(std::chrono::steady_clock::now());
        connected_ = true;
    }
    callbackGate->count_down();
}

void ScrcpySource::ConnectInitial(
    int port,
    int maxRetries,
    std::chrono::milliseconds retryDelay) {
    std::lock_guard<std::mutex> lifecycleLock(lifecycleMutex_);
    std::uint64_t generation;
    {
        std::lock_guard<std::mutex> stateLock(stateMutex_);
        generation = generation_;
    }
    auto retired = RetireCurrent();
    if (retired.control) retired.control->Disconnect();
    if (retired.video) retired.video->Stop();
    retired.video.reset();
    retired.control.reset();

    auto connection = PrepareConnection(port, maxRetries, retryDelay);
    Install(std::move(connection), generation);
}

bool ScrcpySource::Reconnect(int newPort, std::uint64_t requestedGeneration) {
    std::lock_guard<std::mutex> lifecycleLock(lifecycleMutex_);
    {
        std::lock_guard<std::mutex> stateLock(stateMutex_);
        if (requestedGeneration <= generation_) return false;
    }

    auto retired = RetireCurrent();
    if (retired.control) retired.control->Disconnect();
    if (retired.video) retired.video->Stop();
    retired.video.reset();
    retired.control.reset();

    auto connection = PrepareConnection(
        newPort, 20, std::chrono::milliseconds(250));
    Install(std::move(connection), requestedGeneration);
    return true;
}

void ScrcpySource::FanOut(const std::uint8_t* data, size_t size) {
    lastFrameAt_.store(std::chrono::steady_clock::now());
    videoFanout_.SendAccessUnit(data, size);
}

void ScrcpySource::RequestIdr() {
    std::shared_ptr<ScrcpyControlClient> control;
    {
        std::lock_guard<std::mutex> stateLock(stateMutex_);
        control = control_;
    }
    if (control) control->RequestIdr();
}

SourceStatus ScrcpySource::Status() const {
    std::lock_guard<std::mutex> stateLock(stateMutex_);
    SourceHealthState state = SourceHealthState::Disconnected;
    if (connected_ && video_ && control_ && control_->IsConnected() &&
        !video_->LastReadFailed() && !control_->LastSendFailed()) {
        auto idle = std::chrono::steady_clock::now() - lastFrameAt_.load();
        state = idle > stallThreshold_
            ? SourceHealthState::Stalled
            : SourceHealthState::Connected;
    }
    return SourceStatus{state, generation_, width_, height_};
}

std::shared_ptr<ScrcpyControlClient> ScrcpySource::Control() const {
    std::lock_guard<std::mutex> stateLock(stateMutex_);
    return control_;
}
