#include <gtest/gtest.h>
#include "source_video_fanout.h"

#include <cstdint>
#include <initializer_list>
#include <optional>
#include <stdexcept>
#include <string>
#include <vector>

namespace {

std::vector<std::uint8_t> Nalu(
    std::uint8_t header,
    std::initializer_list<std::uint8_t> payload = {}) {
    std::vector<std::uint8_t> result = {0x00, 0x00, 0x00, 0x01, header};
    result.insert(result.end(), payload);
    return result;
}

std::vector<std::uint8_t> Join(
    std::initializer_list<std::vector<std::uint8_t>> parts) {
    std::vector<std::uint8_t> result;
    for (const auto& part : parts) {
        result.insert(result.end(), part.begin(), part.end());
    }
    return result;
}

class CountingPreparer final : public SourceAccessUnitPreparer {
public:
    explicit CountingPreparer(std::vector<std::string>& events)
        : events_(events) {}

    std::optional<std::vector<std::uint8_t>> ObserveAndPrepare(
        const std::uint8_t* data,
        std::size_t size) override {
        ++observeCount;
        events_.push_back("prepare");
        std::vector<std::uint8_t> prepared = {0xAA};
        prepared.insert(prepared.end(), data, data + size);
        return prepared;
    }

    void Reset() override { ++resetCount; }

    int observeCount = 0;
    int resetCount = 0;

private:
    std::vector<std::string>& events_;
};

} // namespace

TEST(SourceVideoFanout, PreparesOnceBeforeSnapshotAndDeliversOnePreparedUnitToEveryPeer) {
    std::vector<std::string> events;
    std::vector<std::uint8_t> firstReceived;
    std::vector<std::uint8_t> secondReceived;
    CountingPreparer preparer(events);
    SourceVideoFanout fanout(
        preparer,
        [&]() {
            events.push_back("snapshot");
            return std::vector<SourceVideoPeerTarget>{
                {"first", [&](const std::uint8_t* data, std::size_t size) {
                    events.push_back("first");
                    firstReceived.assign(data, data + size);
                }},
                {"second", [&](const std::uint8_t* data, std::size_t size) {
                    events.push_back("second");
                    secondReceived.assign(data, data + size);
                }},
            };
        },
        [](const std::string&) {});
    const std::vector<std::uint8_t> input = {0x01, 0x02};

    fanout.SendAccessUnit(input.data(), input.size());

    EXPECT_EQ(preparer.observeCount, 1);
    EXPECT_EQ(events, (std::vector<std::string>{
        "prepare", "snapshot", "first", "second"}));
    EXPECT_EQ(firstReceived, (std::vector<std::uint8_t>{0xAA, 0x01, 0x02}));
    EXPECT_EQ(secondReceived, firstReceived);
}

TEST(SourceVideoFanout, PrependsCachedSpsAndPpsToIdr) {
    H264SourceAccessUnitPreparer preparer;
    std::vector<std::vector<std::uint8_t>> received;
    SourceVideoFanout fanout(
        preparer,
        [&]() {
            return std::vector<SourceVideoPeerTarget>{
                {"peer", [&](const std::uint8_t* data, std::size_t size) {
                    received.emplace_back(data, data + size);
                }},
            };
        },
        [](const std::string&) {});
    auto sps = Nalu(0x67, {0x42, 0xC0, 0x29});
    auto pps = Nalu(0x68, {0xCE, 0x3C, 0x80});
    auto idr = Nalu(0x65, {0xAA, 0xBB});

    fanout.SendAccessUnit(sps.data(), sps.size());
    fanout.SendAccessUnit(pps.data(), pps.size());
    fanout.SendAccessUnit(idr.data(), idr.size());

    ASSERT_EQ(received.size(), 3u);
    EXPECT_EQ(received[2], Join({sps, pps, idr}));
}

TEST(SourceVideoFanout, BeginGenerationClearsCachedSpsAndPps) {
    H264SourceAccessUnitPreparer preparer;
    std::vector<std::vector<std::uint8_t>> received;
    SourceVideoFanout fanout(
        preparer,
        [&]() {
            return std::vector<SourceVideoPeerTarget>{
                {"peer", [&](const std::uint8_t* data, std::size_t size) {
                    received.emplace_back(data, data + size);
                }},
            };
        },
        [](const std::string&) {});
    auto config = Join({
        Nalu(0x67, {0x42, 0xC0, 0x29}),
        Nalu(0x68, {0xCE, 0x3C, 0x80}),
    });
    auto idr = Nalu(0x65, {0x10, 0x20});
    fanout.SendAccessUnit(config.data(), config.size());

    fanout.BeginGeneration();
    fanout.SendAccessUnit(idr.data(), idr.size());

    ASSERT_EQ(received.size(), 2u);
    EXPECT_EQ(received[1], idr);
}

TEST(SourceVideoFanout, RemovesThrowingPeerAndContinuesWithLaterPeers) {
    H264SourceAccessUnitPreparer preparer;
    std::vector<std::string> removed;
    std::vector<std::uint8_t> received;
    SourceVideoFanout fanout(
        preparer,
        [&]() {
            return std::vector<SourceVideoPeerTarget>{
                {"broken", [](const std::uint8_t*, std::size_t) {
                    throw std::runtime_error("send failed");
                }},
                {"healthy", [&](const std::uint8_t* data, std::size_t size) {
                    received.assign(data, data + size);
                }},
            };
        },
        [&](const std::string& id) { removed.push_back(id); });
    auto idr = Nalu(0x65, {0x30});

    testing::internal::CaptureStderr();
    EXPECT_NO_THROW(fanout.SendAccessUnit(idr.data(), idr.size()));
    auto diagnostic = testing::internal::GetCapturedStderr();

    EXPECT_EQ(removed, (std::vector<std::string>{"broken"}));
    EXPECT_EQ(received, idr);
    EXPECT_NE(diagnostic.find("[peer] video send failed id=broken: send failed"),
              std::string::npos);
}

TEST(SourceVideoFanout, DeliversToHealthyPeersBeforeMarkingFailures) {
    H264SourceAccessUnitPreparer preparer;
    std::vector<std::string> events;
    SourceVideoFanout fanout(
        preparer,
        [&]() {
            return std::vector<SourceVideoPeerTarget>{
                {"broken", [](const std::uint8_t*, std::size_t) {
                    throw std::runtime_error("send failed");
                }},
                {"healthy", [&](const std::uint8_t*, std::size_t) {
                    events.push_back("healthy");
                }},
            };
        },
        [&](const std::string& id) { events.push_back("mark:" + id); });
    auto idr = Nalu(0x65, {0x40});

    testing::internal::CaptureStderr();
    fanout.SendAccessUnit(idr.data(), idr.size());
    testing::internal::GetCapturedStderr();

    EXPECT_EQ(events, (std::vector<std::string>{"healthy", "mark:broken"}));
}
