#pragma once
#include <cstdint>
#include <string>

std::string BuildReadyRecord(
    const std::string& instanceName,
    int pid,
    int whepPort,
    int adminPort,
    std::uint64_t generation,
    int width,
    int height);
