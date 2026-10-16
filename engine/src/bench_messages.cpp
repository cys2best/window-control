#include "bench_messages.h"
#include "bench_log.h"

#include <algorithm>
#include <cstdint>

using json = nlohmann::json;

namespace bench {
namespace {

std::optional<std::uint64_t> UnsignedField(const json& msg, const char* field) {
    auto it = msg.find(field);
    if (it == msg.end() || !it->is_number_unsigned()) return std::nullopt;
    return it->get<std::uint64_t>();
}

}  // namespace

bool IsBenchMessage(const std::string& type) {
    return type.rfind("bench.", 0) == 0;
}

bool EnabledFromEnv(const char* value) {
    return value != nullptr && std::string(value) == "1";
}

std::optional<std::string> HandleMessage(const json& msg) {
    if (!Enabled()) return std::nullopt;
    const std::string type = msg.value("type", "");

    if (type == "bench.ping") {
        json reply = {{"type", "bench.pong"}, {"engine_us", NowUs()}};
        if (msg.contains("id")) reply["id"] = msg["id"];
        return reply.dump();
    }
    if (type == "bench.start") {
        Reset();
        return json{{"type", "bench.started"}, {"engine_us", NowUs()}}.dump();
    }
    if (type == "bench.fetch") {
        const std::uint64_t from = UnsignedField(msg, "from").value_or(0);
        const std::uint64_t count = std::clamp<std::uint64_t>(
            UnsignedField(msg, "count").value_or(kMaxFetchCount), 1, kMaxFetchCount);
        const Chunk chunk = Fetch(from, static_cast<std::size_t>(count));

        json entries = json::array();
        for (const auto& entry : chunk.entries) {
            entries.push_back({static_cast<int>(entry.kind), entry.key,
                               entry.t1Us, entry.t2Us, entry.t3Us});
        }
        return json{
            {"type", "bench.chunk"},
            {"from", chunk.from},
            {"total", chunk.total},
            {"capacity", chunk.capacity},
            {"wrapped", chunk.wrapped},
            {"entries", entries},
        }.dump();
    }
    return std::nullopt;
}

}  // namespace bench
