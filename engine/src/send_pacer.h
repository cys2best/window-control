#pragma once

#include <chrono>
#include <cstddef>

// The encoder's configured bitrate for the current scrcpy source, in bits per
// second. One engine process serves one source, so this is process-wide: the
// host sets it at start and again on every source reconnect (a tier change).
// Zero means "not known", which turns pacing off.
namespace video_target {
void SetBitsPerSecond(double bitsPerSecond);
double BitsPerSecond();
}  // namespace video_target

// Decides how long to hold each packet so a frame leaves as a stream instead
// of one burst. A keyframe is tens of packets; sent back to back, a relayed
// or congested path drops a run of them and the decoder then waits for the
// next keyframe. Pacing at a multiple of the encoder's bitrate keeps the
// stream ahead of the encoder while bounding the burst.
//
// Not thread-safe; the caller serialises access.
class SendPacer {
public:
    using Clock = std::chrono::steady_clock;

    // Packets a quiet link may send back to back before pacing starts.
    static constexpr std::size_t kBurstPackets = 6;
    static constexpr std::size_t kPacketBytes = 1200;

    explicit SendPacer(double headroom = 2.0);

    // Encoder bitrate to pace against. Zero or less disables pacing.
    void SetTargetBitsPerSecond(double bitsPerSecond);

    // Accounts for `bytes` that are ready to send at `now` and returns how
    // long to wait before sending them.
    Clock::duration Reserve(std::size_t bytes, Clock::time_point now);

    // The pacing rate in bits per second, or zero when pacing is off.
    double RateBitsPerSecond() const;

private:
    double headroom_;
    double rate_ = 0.0;
    bool started_ = false;
    Clock::time_point nextFree_{};
};
