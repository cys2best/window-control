#include "ready_record.h"
#include <nlohmann/json.hpp>

using json = nlohmann::json;

std::string BuildReadyRecord(
    const std::string& instanceName,
    int pid,
    int whepPort,
    int adminPort,
    std::uint64_t generation,
    int width,
    int height) {
    json record = {
        {"instance_name", instanceName},
        {"pid", pid},
        {"whep_port", whepPort},
        {"admin_port", adminPort},
        {"generation", generation},
        {"width", width},
        {"height", height},
    };
    return record.dump();
}
