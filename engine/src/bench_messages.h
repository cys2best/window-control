#pragma once

#include <cstddef>
#include <nlohmann/json.hpp>
#include <optional>
#include <string>

namespace bench {

constexpr std::size_t kMaxFetchCount = 500;

bool IsBenchMessage(const std::string& type);
// EMUCTRL_BENCH turns bench mode on only when it is exactly "1".
bool EnabledFromEnv(const char* value);
// The reply to send back on the same channel; nullopt when bench mode is off
// or the message needs no reply.
std::optional<std::string> HandleMessage(const nlohmann::json& msg);

}  // namespace bench
