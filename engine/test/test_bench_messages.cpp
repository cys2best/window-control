#include <gtest/gtest.h>
#include "bench_log.h"
#include "bench_messages.h"
#include <nlohmann/json.hpp>

using json = nlohmann::json;

namespace {

class BenchMessagesTest : public ::testing::Test {
protected:
    void SetUp() override { bench::SetEnabled(true); }
    void TearDown() override { bench::SetEnabled(false); }
};

json Reply(const char* message) {
    auto reply = bench::HandleMessage(json::parse(message));
    EXPECT_TRUE(reply.has_value());
    return reply ? json::parse(*reply) : json{};
}

}  // namespace

TEST(BenchMessages, IgnoredWhenBenchIsOff) {
    bench::SetEnabled(false);
    EXPECT_FALSE(bench::HandleMessage(json::parse(R"({"type":"bench.ping","id":1})")));
    EXPECT_FALSE(bench::HandleMessage(json::parse(R"({"type":"bench.fetch"})")));
}

TEST(BenchMessages, RecognisesBenchPrefixOnly) {
    EXPECT_TRUE(bench::IsBenchMessage("bench.ping"));
    EXPECT_FALSE(bench::IsBenchMessage("click"));
    EXPECT_FALSE(bench::IsBenchMessage("benchmark"));
}

TEST(BenchMessages, EnabledFromEnvOnlyForExactlyOne) {
    EXPECT_TRUE(bench::EnabledFromEnv("1"));
    EXPECT_FALSE(bench::EnabledFromEnv("0"));
    EXPECT_FALSE(bench::EnabledFromEnv("true"));
    EXPECT_FALSE(bench::EnabledFromEnv(""));
    EXPECT_FALSE(bench::EnabledFromEnv(nullptr));
}

TEST_F(BenchMessagesTest, PingEchoesIdWithEngineTime) {
    const auto before = bench::NowUs();
    const auto reply = Reply(R"({"type":"bench.ping","id":42})");
    EXPECT_EQ(reply["type"], "bench.pong");
    EXPECT_EQ(reply["id"], 42);
    EXPECT_GE(reply["engine_us"].get<std::int64_t>(), before);
    EXPECT_LE(reply["engine_us"].get<std::int64_t>(), bench::NowUs());
}

TEST_F(BenchMessagesTest, StartClearsTheLog) {
    bench::Record(bench::Entry{bench::EntryKind::Frame, 1, 1, 2, 3});
    EXPECT_EQ(Reply(R"({"type":"bench.start"})")["type"], "bench.started");
    EXPECT_EQ(bench::Fetch(0, 10).total, 0u);
}

TEST_F(BenchMessagesTest, FetchReturnsCompactEntries) {
    bench::Record(bench::Entry{bench::EntryKind::Input, 7, 100, 200, 0});
    bench::Record(bench::Entry{bench::EntryKind::Frame, 9000, 300, 400, 500});

    const auto reply = Reply(R"({"type":"bench.fetch","from":0,"count":10})");
    EXPECT_EQ(reply["type"], "bench.chunk");
    EXPECT_EQ(reply["from"], 0);
    EXPECT_EQ(reply["total"], 2);
    EXPECT_EQ(reply["capacity"], bench::kCapacity);
    EXPECT_EQ(reply["wrapped"], false);
    EXPECT_EQ(reply["entries"], json::parse("[[0,7,100,200,0],[1,9000,300,400,500]]"));
}

TEST_F(BenchMessagesTest, FetchClampsCountAndDefaultsBadFields) {
    for (std::uint64_t i = 0; i < 3; ++i) {
        bench::Record(bench::Entry{bench::EntryKind::Frame, i, 1, 2, 3});
    }
    EXPECT_EQ(Reply(R"({"type":"bench.fetch","from":0,"count":0})")["entries"].size(), 1u);
    EXPECT_EQ(Reply(R"({"type":"bench.fetch","from":0,"count":100000})")["entries"].size(), 3u);
    EXPECT_EQ(Reply(R"({"type":"bench.fetch","from":-5,"count":1.5})")["entries"].size(), 3u);
    EXPECT_EQ(Reply(R"({"type":"bench.fetch","from":99})")["entries"].size(), 0u);
}

TEST_F(BenchMessagesTest, UnknownBenchOpGetsNoReply) {
    EXPECT_FALSE(bench::HandleMessage(json::parse(R"({"type":"bench.nope"})")));
}
