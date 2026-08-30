#include <gtest/gtest.h>
#include "whep_capability.h"

namespace {

constexpr auto kNow = std::chrono::system_clock::time_point(std::chrono::seconds(2000000000));

}  // namespace

TEST(WhepCapability, AllowsEveryTokenWhenSecretIsEmpty) {
    WhepCapabilityConfig config{"", "instance0"};

    EXPECT_TRUE(ValidateWhepCapability(config, "anything-or-nothing", kNow));
    EXPECT_TRUE(ValidateWhepCapability(config, "", kNow));
}

TEST(WhepCapability, RejectsMissingMalformedAndInvalidExpiryTokens) {
    WhepCapabilityConfig config{"secret", "instance0"};

    EXPECT_FALSE(ValidateWhepCapability(config, "", kNow));
    EXPECT_FALSE(ValidateWhepCapability(config, "not-enough-parts", kNow));
    EXPECT_FALSE(ValidateWhepCapability(config, "123.instance0", kNow));
    EXPECT_FALSE(ValidateWhepCapability(config, "not-a-number.instance0.deadbeef", kNow));
    EXPECT_FALSE(ValidateWhepCapability(config, "100.instance0.728cfd97e102ab7a412a4eec5d2c39e4c1494cdcaa134632d7aec7065f3caf6f", kNow));
}

TEST(WhepCapability, RejectsWrongInstanceAndTamperedSignature) {
    WhepCapabilityConfig config{"secret", "instance0"};

    EXPECT_FALSE(ValidateWhepCapability(config, "4102444800.instance1.c3e2e0c219710589db54a974438715acbaf66c9ec3d6022261128c95f563dc2e", kNow));
    EXPECT_FALSE(ValidateWhepCapability(config, "4102444800.instance0.c3e2e0c219710589db54a974438715acbaf66c9ec3d6022261128c95f563dc20", kNow));
}

TEST(WhepCapability, AcceptsUnexpiredPythonCompatibleHmacFixture) {
    WhepCapabilityConfig config{"secret", "instance0"};

    // Literal calculated independently with Python-compatible HMAC-SHA256:
    // hmac.new(b"secret", b"4102444800.instance0", hashlib.sha256).hexdigest()
    EXPECT_TRUE(ValidateWhepCapability(
        config,
        "4102444800.instance0.c3e2e0c219710589db54a974438715acbaf66c9ec3d6022261128c95f563dc2e",
        kNow));
}
