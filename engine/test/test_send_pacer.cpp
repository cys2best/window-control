#include <gtest/gtest.h>
#include "send_pacer.h"

#include <chrono>
#include <cstddef>

namespace {

using Clock = SendPacer::Clock;
using std::chrono::duration;
using std::chrono::duration_cast;
using std::chrono::milliseconds;

constexpr std::size_t kPacket = SendPacer::kPacketBytes;

double Seconds(Clock::duration value) {
    return duration<double>(value).count();
}

struct FrameResult {
    double seconds = 0;
    std::size_t largestBurstPackets = 0;
};

// Sends one frame the way the media handler does: reserve each packet, wait
// out the delay, send. `oversleep` models a caller that wakes late.
FrameResult SendFrame(SendPacer& pacer, std::size_t bytes, Clock::time_point& now,
                      Clock::duration oversleep = Clock::duration::zero()) {
    const Clock::time_point start = now;
    FrameResult result;
    std::size_t burst = 0;
    for (std::size_t sent = 0; sent < bytes; sent += kPacket) {
        const std::size_t packet = bytes - sent < kPacket ? bytes - sent : kPacket;
        const Clock::duration delay = pacer.Reserve(packet, now);
        if (delay > Clock::duration::zero()) {
            burst = 0;
            now += delay + oversleep;
        }
        burst += 1;
        if (burst > result.largestBurstPackets) result.largestBurstPackets = burst;
    }
    result.seconds = Seconds(now - start);
    return result;
}

// Plays `seconds` of video at `fps` with every frame `frameBytes` large and
// returns how long it took to send.
double Play(SendPacer& pacer, double seconds, int fps, std::size_t frameBytes,
            Clock::time_point& now) {
    const Clock::time_point start = now;
    const auto interval = duration_cast<Clock::duration>(duration<double>(1.0 / fps));
    const int frames = static_cast<int>(seconds * fps);
    for (int i = 0; i < frames; ++i) {
        const Clock::time_point due = start + interval * (i + 1);
        SendFrame(pacer, frameBytes, now);
        if (now < due) now = due;
    }
    return Seconds(now - start);
}

TEST(SendPacer, PacesAtTwiceTheEncoderBitrate) {
    SendPacer pacer;
    EXPECT_DOUBLE_EQ(pacer.RateBitsPerSecond(), 0.0);
    pacer.SetTargetBitsPerSecond(800'000.0);
    EXPECT_DOUBLE_EQ(pacer.RateBitsPerSecond(), 1'600'000.0);
}

TEST(SendPacer, WithoutATargetNothingIsDelayed) {
    SendPacer pacer;
    Clock::time_point now{};
    EXPECT_EQ(SendFrame(pacer, 300'000, now).seconds, 0.0);

    pacer.SetTargetBitsPerSecond(800'000.0);
    pacer.SetTargetBitsPerSecond(0.0);
    EXPECT_EQ(SendFrame(pacer, 300'000, now).seconds, 0.0);
    pacer.SetTargetBitsPerSecond(-5.0);
    EXPECT_EQ(SendFrame(pacer, 300'000, now).seconds, 0.0);
}

TEST(SendPacer, OrdinaryFramesLeaveWithoutAnyDelay) {
    // 360p tier: about 330 kbps of one- and two-packet frames.
    SendPacer pacer;
    pacer.SetTargetBitsPerSecond(800'000.0);
    Clock::time_point now{};
    for (int i = 0; i < 90; ++i) {
        EXPECT_EQ(SendFrame(pacer, 1400, now).seconds, 0.0) << "frame " << i;
        now += milliseconds(33);
    }
}

TEST(SendPacer, KeyframeOnTheLowTierIsSpreadOutNotBurst) {
    // Over a relay that carries about 1.7 Mbps, a 60 KB keyframe sent as one
    // burst lost a third of its packets. Paced at 1.6 Mbps it takes about a
    // quarter of a second and never sends more than a handful back to back.
    SendPacer pacer;
    pacer.SetTargetBitsPerSecond(800'000.0);
    Clock::time_point now{};
    Play(pacer, 2.0, 30, 1400, now);

    const FrameResult keyframe = SendFrame(pacer, 60'000, now);

    EXPECT_LE(keyframe.largestBurstPackets, SendPacer::kBurstPackets + 1);
    EXPECT_GE(keyframe.seconds, 0.20);
    EXPECT_LE(keyframe.seconds, 0.30);
}

TEST(SendPacer, HighTierKeyframeIsDelayedOnlyBriefly) {
    // 1080p tier, 8 Mbps: a 250 KB keyframe must not add noticeable lag.
    SendPacer pacer;
    pacer.SetTargetBitsPerSecond(8'000'000.0);
    Clock::time_point now{};

    EXPECT_LE(SendFrame(pacer, 250'000, now).seconds, 0.13);
}

TEST(SendPacer, AStreamAtItsFullBitrateIsNeverSlowedDown) {
    // The pacer must not become the bottleneck: four seconds of video at the
    // encoder's full bitrate has to leave in four seconds, with no frame
    // waiting at all.
    SendPacer pacer;
    pacer.SetTargetBitsPerSecond(8'000'000.0);
    Clock::time_point now{};

    // (The frame interval is rounded to the clock's resolution.)
    EXPECT_NEAR(Play(pacer, 4.0, 60, 16'667, now), 4.0, 0.001);
}

TEST(SendPacer, AnEncoderOvershootingByHalfStillKeepsUp) {
    // bitrate-mode is a hint; encoders overshoot. 12 Mbps against an 8 Mbps
    // target is still under the 16 Mbps pacing rate.
    SendPacer pacer;
    pacer.SetTargetBitsPerSecond(8'000'000.0);
    Clock::time_point now{};

    EXPECT_LE(Play(pacer, 4.0, 60, 25'000, now), 4.01);
}

TEST(SendPacer, IdleTimeDoesNotBuyALargerBurst) {
    SendPacer pacer;
    pacer.SetTargetBitsPerSecond(800'000.0);
    Clock::time_point now{};
    SendFrame(pacer, 1400, now);
    now += std::chrono::seconds(30);

    const FrameResult keyframe = SendFrame(pacer, 60'000, now);

    EXPECT_LE(keyframe.largestBurstPackets, SendPacer::kBurstPackets + 1);
}

TEST(SendPacer, ACallerThatWakesLateCatchesUpWithinTheBurstAllowance) {
    // Windows sleeps overshoot by up to ~15 ms.
    SendPacer pacer;
    pacer.SetTargetBitsPerSecond(800'000.0);
    Clock::time_point now{};

    const FrameResult keyframe = SendFrame(pacer, 60'000, now, milliseconds(15));

    EXPECT_LE(keyframe.largestBurstPackets, SendPacer::kBurstPackets + 1);
    EXPECT_LE(keyframe.seconds, 0.60);
}

TEST(SendPacer, ALoweredTargetTakesEffectOnTheNextPacket) {
    SendPacer pacer;
    pacer.SetTargetBitsPerSecond(8'000'000.0);
    Clock::time_point now{};
    SendFrame(pacer, 30'000, now);

    pacer.SetTargetBitsPerSecond(800'000.0);
    now += std::chrono::seconds(1);

    EXPECT_GE(SendFrame(pacer, 60'000, now).seconds, 0.20);
}

TEST(SendPacer, ZeroBytesAreHarmless) {
    SendPacer pacer;
    pacer.SetTargetBitsPerSecond(800'000.0);
    Clock::time_point now{};
    EXPECT_EQ(pacer.Reserve(0, now), Clock::duration::zero());
}

TEST(VideoTarget, RoundTripsAndTreatsNonPositiveAsUnknown) {
    video_target::SetBitsPerSecond(2'000'000.0);
    EXPECT_DOUBLE_EQ(video_target::BitsPerSecond(), 2'000'000.0);
    video_target::SetBitsPerSecond(-1.0);
    EXPECT_DOUBLE_EQ(video_target::BitsPerSecond(), 0.0);
    video_target::SetBitsPerSecond(0.0);
    EXPECT_DOUBLE_EQ(video_target::BitsPerSecond(), 0.0);
}

}  // namespace
