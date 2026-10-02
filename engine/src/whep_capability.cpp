#include "whep_capability.h"

#include <openssl/evp.h>
#include <openssl/hmac.h>

#include <array>
#include <cstdint>
#include <exception>
#include <limits>
#include <string_view>

namespace {

std::string HexHmacSha256(std::string_view secret, std::string_view payload) {
    std::array<unsigned char, EVP_MAX_MD_SIZE> digest{};
    unsigned int digestLength = 0;
    HMAC(EVP_sha256(), secret.data(), static_cast<int>(secret.size()),
         reinterpret_cast<const unsigned char*>(payload.data()), payload.size(),
         digest.data(), &digestLength);

    static constexpr char kHex[] = "0123456789abcdef";
    std::string result;
    result.reserve(digestLength * 2);
    for (unsigned int i = 0; i < digestLength; ++i) {
        result.push_back(kHex[digest[i] >> 4]);
        result.push_back(kHex[digest[i] & 0x0f]);
    }
    return result;
}

bool ConstantTimeEquals(std::string_view left, std::string_view right) {
    if (left.size() != right.size()) return false;

    unsigned char diff = 0;
    for (size_t i = 0; i < left.size(); ++i) {
        diff |= static_cast<unsigned char>(left[i] ^ right[i]);
    }
    return diff == 0;
}

bool ParseExpiry(std::string_view value, std::int64_t* expiry) {
    if (value.empty()) return false;
    try {
        size_t parsed = 0;
        const auto result = std::stoll(std::string(value), &parsed);
        if (parsed != value.size()) return false;
        *expiry = result;
        return true;
    } catch (const std::exception&) {
        return false;
    }
}

}  // namespace

bool ValidateWhepCapability(
    const WhepCapabilityConfig& config, const std::string& bearerToken,
    std::chrono::system_clock::time_point now) {
    if (config.secret.empty()) return true;
    if (bearerToken.empty() || config.secret.size() > std::numeric_limits<int>::max()) {
        return false;
    }

    const size_t firstDot = bearerToken.find('.');
    const size_t lastDot = bearerToken.rfind('.');
    if (firstDot == std::string::npos || firstDot == lastDot) return false;

    const std::string_view expiryString(bearerToken.data(), firstDot);
    const std::string_view instance(
        bearerToken.data() + firstDot + 1, lastDot - firstDot - 1);
    const std::string_view signature(bearerToken.data() + lastDot + 1,
                                     bearerToken.size() - lastDot - 1);
    if (instance.empty() || signature.empty() || instance != config.instanceName) return false;

    std::int64_t expiry = 0;
    if (!ParseExpiry(expiryString, &expiry)) return false;

    const std::string payload = bearerToken.substr(0, lastDot);
    if (!ConstantTimeEquals(signature, HexHmacSha256(config.secret, payload))) return false;

    const auto nowUnix = std::chrono::duration_cast<std::chrono::seconds>(
        now.time_since_epoch()).count();
    return nowUnix <= expiry;
}
