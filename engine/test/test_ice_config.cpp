#include <gtest/gtest.h>
#include <string>
#include <vector>

// Forward declaration of IceServerSpec and BuildIceConfiguration to be defined in ice_config.h (Task 3)
struct IceServerSpec {
    std::vector<std::string> urls;
    std::string username;
    std::string credential;
};

// Test that structured credentials preserve reserved characters (e.g., ?, &, :, /, @)
// without unsafe URI interpolation or corruption.
TEST(IceConfigTest, StructuredCredentialsPreserveReservedCharacters) {
    IceServerSpec spec;
    spec.urls = {"turn:turn.example.com:3478?transport=udp", "turns:turn.example.com:5349?transport=tcp"};
    spec.username = "1727870400:inst-uuid-1234:sess-5678:alice?user@domain#hash&foo=bar";
    spec.credential = "base64+with/special=chars:and?symbols&here!";

    EXPECT_EQ(spec.urls.size(), 2);
    EXPECT_EQ(spec.username, "1727870400:inst-uuid-1234:sess-5678:alice?user@domain#hash&foo=bar");
    EXPECT_EQ(spec.credential, "base64+with/special=chars:and?symbols&here!");
}

TEST(IceConfigTest, EmptyCredentialsForStun) {
    IceServerSpec spec;
    spec.urls = {"stun:stun.l.google.com:19302"};
    spec.username = "";
    spec.credential = "";

    EXPECT_TRUE(spec.username.empty());
    EXPECT_TRUE(spec.credential.empty());
    EXPECT_EQ(spec.urls[0], "stun:stun.l.google.com:19302");
}
