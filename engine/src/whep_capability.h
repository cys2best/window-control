#pragma once

#include <chrono>
#include <string>

struct WhepCapabilityConfig {
    std::string secret;       // Empty disables capability authentication.
    std::string instanceName;
};

// Validates a bearer token shaped as
// "<expiry_unix>.<instance_name>.<hmac_sha256_hex>".
bool ValidateWhepCapability(
    const WhepCapabilityConfig& config,
    const std::string& bearerToken,
    std::chrono::system_clock::time_point now = std::chrono::system_clock::now());
