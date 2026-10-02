#include <gtest/gtest.h>
#include "ice_config.h"

// Test that structured credentials preserve reserved characters (e.g., ?, &, :, /, @)
// without unsafe URI interpolation or corruption.
TEST(IceConfigTest, StructuredCredentialsPreserveReservedCharacters) {
    IceServerSpec spec;
    spec.urls = {"turn:turn.example.com:3478?transport=udp", "turns:turn.example.com:5349?transport=tcp"};
    spec.username = "1727870400:inst-uuid-1234:sess-5678:alice?user@domain#hash&foo=bar";
    spec.credential = "base64+with/special=chars:and?symbols&here!";

    std::vector<IceServerSpec> specs = {spec};
    auto config = BuildIceConfiguration(specs);

    ASSERT_EQ(config.iceServers.size(), 2);
    // Note: rtc::IceServer doesn't expose url directly, but we can check username and credential
    EXPECT_EQ(config.iceServers[0].username, "1727870400:inst-uuid-1234:sess-5678:alice?user@domain#hash&foo=bar");
    EXPECT_EQ(config.iceServers[0].password, "base64+with/special=chars:and?symbols&here!");
    EXPECT_EQ(config.iceServers[1].username, "1727870400:inst-uuid-1234:sess-5678:alice?user@domain#hash&foo=bar");
    EXPECT_EQ(config.iceServers[1].password, "base64+with/special=chars:and?symbols&here!");
}

TEST(IceConfigTest, EmptyCredentialsForStun) {
    IceServerSpec spec;
    spec.urls = {"stun:stun.l.google.com:19302"};
    spec.username = "";
    spec.credential = "";

    std::vector<IceServerSpec> specs = {spec};
    auto config = BuildIceConfiguration(specs);

    ASSERT_EQ(config.iceServers.size(), 1);
    EXPECT_TRUE(config.iceServers[0].username.empty());
    EXPECT_TRUE(config.iceServers[0].password.empty());
}
