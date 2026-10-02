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

TEST(IceConfigTest, PreservesEveryTransportAndBothAddressFamilies) {
    auto config = BuildIceConfiguration({{{"turn:relay.example:3478?transport=udp",
                                         "turn:relay.example:3478?transport=tcp",
                                         "turns:[2001:db8::1]:5349?transport=tcp"}, "u", "p"}});
    ASSERT_EQ(config.iceServers.size(), 3u);
    EXPECT_EQ(config.iceServers[0].relayType, rtc::IceServer::RelayType::TurnUdp);
    EXPECT_EQ(config.iceServers[1].relayType, rtc::IceServer::RelayType::TurnTcp);
    EXPECT_EQ(config.iceServers[2].relayType, rtc::IceServer::RelayType::TurnTls);
    EXPECT_EQ(config.iceServers[2].hostname, "2001:db8::1");
    EXPECT_EQ(config.iceTransportPolicy, rtc::TransportPolicy::All);
    EXPECT_FALSE(config.bindAddress.has_value());
}

TEST(IceConfigTest, RejectsMalformedUrlsAndMissingTurnCredentials) {
    for (const auto& url : {"", "https://relay.example", "turn:user@relay.example",
                           "turn:relay.example:0", "turn:relay.example:65536",
                           "turn:relay.example?transport=quic", "turns:relay.example?transport=udp",
                           "stun:relay.example?transport=tcp", "turn:relay.example/path",
                           "turn:[garbage]:3478", "turn:relay.example?transport=udp&x=1"}) {
        EXPECT_THROW(BuildIceConfiguration({{{url}, "u", "p"}}), std::invalid_argument) << url;
    }
    EXPECT_THROW(BuildIceConfiguration({{{}, "u", "p"}}), std::invalid_argument);
    EXPECT_THROW(BuildIceConfiguration({{{"turn:relay.example"}, "", ""}}), std::invalid_argument);
    EXPECT_THROW(BuildIceConfiguration({{{"stun:relay.example"}, "u", "p"}}), std::invalid_argument);
}

TEST(IceConfigTest, BoundsUntrustedServerListsAndCredentials) {
    EXPECT_THROW(BuildIceConfiguration(std::vector<IceServerSpec>(17, {{"stun:example.com"}, "", ""})), std::invalid_argument);
    EXPECT_THROW(BuildIceConfiguration({{std::vector<std::string>(33, "stun:example.com"), "", ""}}), std::invalid_argument);
    EXPECT_THROW(BuildIceConfiguration({{{"turn:example.com"}, "u", std::string(1025, 'p')}}), std::invalid_argument);
}
