#include "h264_nalu.h"

namespace h264 {
namespace {

std::size_t FindStartCode(
    const std::uint8_t* data,
    std::size_t size,
    std::size_t from,
    std::size_t& startCodeSize) {
    for (std::size_t i = from; i + 3 <= size; ++i) {
        if (i + 4 <= size && data[i] == 0 && data[i + 1] == 0 &&
            data[i + 2] == 0 && data[i + 3] == 1) {
            startCodeSize = 4;
            return i;
        }
        if (data[i] == 0 && data[i + 1] == 0 && data[i + 2] == 1) {
            startCodeSize = 3;
            return i;
        }
    }
    startCodeSize = 0;
    return size;
}

NaluType Classify(std::uint8_t header) {
    switch (header & 0x1F) {
        case 1: return NaluType::Slice;
        case 5: return NaluType::Idr;
        case 7: return NaluType::Sps;
        case 8: return NaluType::Pps;
        default: return NaluType::Other;
    }
}

template <typename Callback>
void ForEachNalu(
    const std::uint8_t* data,
    std::size_t size,
    Callback callback) {
    if (data == nullptr || size == 0) return;

    std::size_t searchFrom = 0;
    while (searchFrom < size) {
        std::size_t startCodeSize = 0;
        std::size_t start = FindStartCode(
            data, size, searchFrom, startCodeSize);
        if (start == size) return;

        std::size_t header = start + startCodeSize;
        if (header >= size) return;

        std::size_t nextStartCodeSize = 0;
        std::size_t end = FindStartCode(
            data, size, header + 1, nextStartCodeSize);
        callback(Classify(data[header]), start, end);
        searchFrom = end;
    }
}

} // namespace

bool ContainsNaluType(
    const std::uint8_t* data,
    std::size_t size,
    NaluType type) {
    bool found = false;
    ForEachNalu(data, size, [&](NaluType current, std::size_t, std::size_t) {
        if (current == type) found = true;
    });
    return found;
}

std::optional<std::vector<std::uint8_t>>
SpsPpsCache::ObserveAndPrepare(
    const std::uint8_t* data,
    std::size_t size) {
    bool carriesSps = false;
    bool carriesPps = false;
    bool carriesIdr = false;

    ForEachNalu(data, size, [&](NaluType type, std::size_t start, std::size_t end) {
        if (type == NaluType::Sps) {
            sps_.assign(data + start, data + end);
            carriesSps = true;
        } else if (type == NaluType::Pps) {
            pps_.assign(data + start, data + end);
            carriesPps = true;
        } else if (type == NaluType::Idr) {
            carriesIdr = true;
        }
    });

    if (!carriesIdr || !HasConfig() || (carriesSps && carriesPps)) {
        return std::nullopt;
    }

    std::vector<std::uint8_t> prepared;
    prepared.reserve(sps_.size() + pps_.size() + size);
    prepared.insert(prepared.end(), sps_.begin(), sps_.end());
    prepared.insert(prepared.end(), pps_.begin(), pps_.end());
    prepared.insert(prepared.end(), data, data + size);
    return prepared;
}

void SpsPpsCache::Reset() {
    sps_.clear();
    pps_.clear();
}

bool SpsPpsCache::HasConfig() const {
    return !sps_.empty() && !pps_.empty();
}

} // namespace h264
