#include "bench_log.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <mutex>

namespace bench {
namespace {

std::atomic<bool> g_enabled{false};
std::mutex g_mutex;
std::vector<Entry> g_buffer;  // sized to kCapacity on first enable
std::uint64_t g_total = 0;
thread_local std::int64_t t_frameReadUs = 0;

}  // namespace

void SetEnabled(bool enabled) {
    std::lock_guard<std::mutex> lock(g_mutex);
    if (enabled && g_buffer.size() != kCapacity) g_buffer.assign(kCapacity, Entry{});
    g_total = 0;
    g_enabled.store(enabled);
}

bool Enabled() {
    return g_enabled.load(std::memory_order_relaxed);
}

std::int64_t NowUs() {
    using namespace std::chrono;
    return duration_cast<microseconds>(steady_clock::now().time_since_epoch()).count();
}

void Reset() {
    std::lock_guard<std::mutex> lock(g_mutex);
    g_total = 0;
}

void Record(const Entry& entry) {
    if (!Enabled()) return;
    std::lock_guard<std::mutex> lock(g_mutex);
    if (g_buffer.empty()) return;
    g_buffer[g_total % kCapacity] = entry;
    ++g_total;
}

Chunk Fetch(std::uint64_t from, std::size_t maxCount) {
    std::lock_guard<std::mutex> lock(g_mutex);
    Chunk chunk;
    chunk.total = g_total;
    chunk.capacity = kCapacity;
    const std::uint64_t oldest = g_total > kCapacity ? g_total - kCapacity : 0;
    chunk.wrapped = from < oldest;
    chunk.from = std::min(std::max(from, oldest), g_total);
    if (g_buffer.empty()) return chunk;

    const std::uint64_t count = std::min<std::uint64_t>(maxCount, g_total - chunk.from);
    chunk.entries.reserve(static_cast<std::size_t>(count));
    for (std::uint64_t i = 0; i < count; ++i) {
        chunk.entries.push_back(g_buffer[(chunk.from + i) % kCapacity]);
    }
    return chunk;
}

void MarkFrameRead() {
    if (Enabled()) t_frameReadUs = NowUs();
}

std::int64_t CurrentFrameReadUs() {
    return t_frameReadUs;
}

}  // namespace bench
