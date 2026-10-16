#include <gtest/gtest.h>
#include "bench_log.h"

namespace {

class BenchLogTest : public ::testing::Test {
protected:
    void SetUp() override { bench::SetEnabled(true); }
    void TearDown() override { bench::SetEnabled(false); }
};

bench::Entry FrameEntry(std::uint64_t key) {
    return bench::Entry{bench::EntryKind::Frame, key, 1, 2, 3};
}

}  // namespace

TEST(BenchLog, RecordIsNoopWhenDisabled) {
    bench::SetEnabled(false);
    bench::Record(FrameEntry(1));
    EXPECT_EQ(bench::Fetch(0, 10).total, 0u);
}

TEST_F(BenchLogTest, FetchesRecordedEntriesInOrder) {
    bench::Record(FrameEntry(10));
    bench::Record(bench::Entry{bench::EntryKind::Input, 7, 100, 200, 0});

    const auto chunk = bench::Fetch(0, 10);
    EXPECT_EQ(chunk.total, 2u);
    EXPECT_EQ(chunk.from, 0u);
    EXPECT_EQ(chunk.capacity, bench::kCapacity);
    EXPECT_FALSE(chunk.wrapped);
    ASSERT_EQ(chunk.entries.size(), 2u);
    EXPECT_EQ(chunk.entries[0].kind, bench::EntryKind::Frame);
    EXPECT_EQ(chunk.entries[0].key, 10u);
    EXPECT_EQ(chunk.entries[1].kind, bench::EntryKind::Input);
    EXPECT_EQ(chunk.entries[1].t2Us, 200);
}

TEST_F(BenchLogTest, FetchHonoursFromAndMaxCount) {
    for (std::uint64_t i = 0; i < 5; ++i) bench::Record(FrameEntry(i));

    const auto chunk = bench::Fetch(2, 2);
    EXPECT_EQ(chunk.from, 2u);
    ASSERT_EQ(chunk.entries.size(), 2u);
    EXPECT_EQ(chunk.entries[0].key, 2u);
    EXPECT_EQ(chunk.entries[1].key, 3u);
}

TEST_F(BenchLogTest, FetchPastTheEndReturnsEmptyChunk) {
    bench::Record(FrameEntry(0));
    const auto chunk = bench::Fetch(5, 10);
    EXPECT_EQ(chunk.total, 1u);
    EXPECT_EQ(chunk.from, 1u);
    EXPECT_TRUE(chunk.entries.empty());
    EXPECT_FALSE(chunk.wrapped);
}

TEST_F(BenchLogTest, WraparoundKeepsNewestAndFlagsOlderFrom) {
    for (std::uint64_t i = 0; i < bench::kCapacity + 10; ++i) bench::Record(FrameEntry(i));

    const auto chunk = bench::Fetch(0, 3);
    EXPECT_TRUE(chunk.wrapped);
    EXPECT_EQ(chunk.from, 10u);
    ASSERT_EQ(chunk.entries.size(), 3u);
    EXPECT_EQ(chunk.entries[0].key, 10u);
    EXPECT_EQ(chunk.entries[2].key, 12u);
}

TEST_F(BenchLogTest, ResetDiscardsEntries) {
    bench::Record(FrameEntry(0));
    bench::Reset();
    EXPECT_EQ(bench::Fetch(0, 10).total, 0u);
}

TEST_F(BenchLogTest, MarkFrameReadStoresTimeOnlyWhenEnabled) {
    bench::MarkFrameRead();
    const auto marked = bench::CurrentFrameReadUs();
    EXPECT_GT(marked, 0);
    EXPECT_LE(marked, bench::NowUs());

    bench::SetEnabled(false);
    bench::MarkFrameRead();
    EXPECT_EQ(bench::CurrentFrameReadUs(), marked);
}
