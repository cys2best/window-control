#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <vector>

namespace h264 {

enum class NaluType : std::uint8_t {
    Other = 0,
    Slice = 1,
    Idr = 5,
    Sps = 7,
    Pps = 8,
};

bool ContainsNaluType(
    const std::uint8_t* data,
    std::size_t size,
    NaluType type);

class SpsPpsCache {
public:
    std::optional<std::vector<std::uint8_t>> ObserveAndPrepare(
        const std::uint8_t* data,
        std::size_t size);

    bool HasConfig() const;

private:
    std::vector<std::uint8_t> sps_;
    std::vector<std::uint8_t> pps_;
};

} // namespace h264
