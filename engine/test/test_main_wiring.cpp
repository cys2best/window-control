#include <gtest/gtest.h>
#include "engine_config.h"
#include <cstdlib>

TEST(EngineConfig, ParsesCommaSeparatedIceServers) {
    auto servers = ParseCommaSeparatedList("stun:a.example:3478,turn:b.example:3478");
    ASSERT_EQ(servers.size(), 2u);
    EXPECT_EQ(servers[0], "stun:a.example:3478");
    EXPECT_EQ(servers[1], "turn:b.example:3478");
}

TEST(EngineConfig, EmptyStringYieldsEmptyList) {
    EXPECT_TRUE(ParseCommaSeparatedList("").empty());
}

TEST(EngineConfig, SinglePlainEntryYieldsOneElement) {
    auto servers = ParseCommaSeparatedList("stun:only.example:3478");
    ASSERT_EQ(servers.size(), 1u);
    EXPECT_EQ(servers[0], "stun:only.example:3478");
}
