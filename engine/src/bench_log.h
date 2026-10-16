#pragma once

#include <cstddef>
#include <cstdint>
#include <vector>

// Lab-only latency log. Off unless EMUCTRL_BENCH=1 (see bench_messages.h);
// when off every call here is a cheap no-op.
namespace bench {

enum class EntryKind : std::uint8_t { Input = 0, Frame = 1 };

// Input: key = bench_seq, t1 = message received, t2 = touch injected, t3 = 0.
// Frame: key = RTP timestamp, t1 = read off the scrcpy socket,
//        t2 = handed to the packetizer, t3 = pacer released the last packet.
struct Entry {
    EntryKind kind = EntryKind::Input;
    std::uint64_t key = 0;
    std::int64_t t1Us = 0;
    std::int64_t t2Us = 0;
    std::int64_t t3Us = 0;
};

struct Chunk {
    std::uint64_t from = 0;     // absolute index of entries[0]
    std::uint64_t total = 0;    // entries recorded since the last Reset
    std::size_t capacity = 0;
    bool wrapped = false;       // the requested `from` was already overwritten
    std::vector<Entry> entries;
};

constexpr std::size_t kCapacity = 65536;

void SetEnabled(bool enabled);
bool Enabled();
std::int64_t NowUs();
void Reset();
void Record(const Entry& entry);
Chunk Fetch(std::uint64_t from, std::size_t maxCount);

// The source delivers each access unit to every peer on its read thread, so
// the read time travels to PeerSession through a thread_local.
void MarkFrameRead();
std::int64_t CurrentFrameReadUs();

}  // namespace bench
