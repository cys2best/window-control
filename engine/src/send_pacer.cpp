#include "send_pacer.h"

#include <algorithm>
#include <atomic>

namespace {

std::atomic<double> g_videoTargetBitsPerSecond{0.0};

// A quiet link may also send this much time's worth of data at once, so an
// ordinary frame at a high bitrate is never held back.
constexpr double kBurstSeconds = 0.020;

SendPacer::Clock::duration Seconds(double seconds) {
    return std::chrono::duration_cast<SendPacer::Clock::duration>(
        std::chrono::duration<double>(seconds));
}

}  // namespace

namespace video_target {

void SetBitsPerSecond(double bitsPerSecond) {
    g_videoTargetBitsPerSecond.store(bitsPerSecond > 0.0 ? bitsPerSecond : 0.0);
}

double BitsPerSecond() {
    return g_videoTargetBitsPerSecond.load();
}

}  // namespace video_target

SendPacer::SendPacer(double headroom) : headroom_(headroom) {}

void SendPacer::SetTargetBitsPerSecond(double bitsPerSecond) {
    rate_ = bitsPerSecond > 0.0 ? bitsPerSecond * headroom_ : 0.0;
}

double SendPacer::RateBitsPerSecond() const {
    return rate_;
}

SendPacer::Clock::duration SendPacer::Reserve(std::size_t bytes, Clock::time_point now) {
    if (rate_ <= 0.0) {
        started_ = false;
        return Clock::duration::zero();
    }

    const double burstBytes = std::max(
        static_cast<double>(kBurstPackets * kPacketBytes), rate_ / 8.0 * kBurstSeconds);
    const Clock::duration credit = Seconds(burstBytes * 8.0 / rate_);

    // nextFree_ is when the link is next free at the pacing rate. It may lag
    // `now` by at most the burst credit, so idle time (or a caller that woke
    // late) never buys a larger burst.
    if (!started_ || nextFree_ < now - credit) {
        started_ = true;
        nextFree_ = now - credit;
    }

    const Clock::duration delay =
        nextFree_ > now ? nextFree_ - now : Clock::duration::zero();
    nextFree_ += Seconds(static_cast<double>(bytes) * 8.0 / rate_);
    return delay;
}
