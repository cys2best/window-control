# Latency Bench Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One command on the Mac runs N scripted taps against an LDPlayer bench instance and writes tap-to-photon p50/p95/p99 plus a per-stage latency breakdown, and two result files can be compared.

**Architecture:** The engine (C++) records input and per-frame timestamps into a fixed ring buffer and answers `bench.*` messages on the existing `input` DataChannel, only when `EMUCTRL_BENCH=1`. A hidden `/bench` page in `apps/web` connects with `connectWhep`, syncs clocks, taps the screen centre, detects the black↔white flip with `requestVideoFrameCallback`, fetches the engine log and runs the analysis (TypeScript in `packages/core/src/bench/`). A Playwright script drives Chrome and writes JSON + Markdown.

**Tech Stack:** C++20 + libdatachannel + nlohmann-json + gtest (engine, Windows only); Python 3 + FastAPI + pytest (host); TypeScript + Jest (`packages/core`, `apps/web`, Next 15 static export); Node ≥ 20 + Playwright (runner).

**Spec:** `docs/superpowers/specs/2026-10-16-latency-bench-harness-design.md`

## Global Constraints

- Bench mode is off unless the host process environment has `EMUCTRL_BENCH` equal to exactly `1`. Off means: no `/bench` routes, `bench.*` messages ignored, nothing recorded.
- The engine inherits the host's environment (`src/server/engine_process.py` copies `os.environ`), so no new engine CLI arguments.
- Engine ring buffer capacity: `65536` entries, preallocated once; no allocation on record.
- `bench.fetch` returns at most `500` entries per message.
- Engine timestamps: `std::chrono::steady_clock` microseconds. Receiver timestamps: `performance.now()` milliseconds.
- Run parameters: default N = `200` taps, `10` warm-up taps (discarded), `50` clock-sync pings before and after, miss timeout `2000` ms, inter-tap gap uniform `300–700` ms, stable-target check `1000` ms.
- Validity: drift > `2` ms (`2000` µs) → cross-clock stages unreliable; miss rate > `5%` → run failed; transport change → run failed; engine log wrapped/missing → breakdown omitted.
- Luminance thresholds: white ≥ `160`, black ≤ `96` (0–255 Rec.601 luma).
- Only new pairing-exempt paths: `/bench`, `/bench.txt`, `/bench/target.html` — all static, all bench-mode only.
- Python commands use `uv run pytest` / `uv run python`, never `python -m`.
- JS tests: `npm run test:core` (packages/core), `npm test -w apps/web`. Runner tests: `node --test scripts/bench/`.
- Engine builds and tests run on Windows only (`engine/CMakeLists.txt` refuses non-WIN32). On the Mac, push and use the `build-engine` CI job in `.github/workflows/build.yml`; on Windows: `cmake --build engine\build --config Release` then `engine\build\Release\engine_tests.exe`.
- Commits: Conventional Commits (`COMMIT_CONVENTION.md`); no plan/task numbers, no AI attribution, no Co-Authored-By.
- `docs/` is git-ignored except tracked files; add files under `docs/superpowers/` with `git add -f`.

## Review Focus

- Stream reconnect or tier change mid-run restarts the engine's RTP timestamps, so tap frames no longer match engine entries → breakdown must be omitted with a reason, not filled with garbage (test in Task 7).
- A second viewer (the phone) connected during the run adds frame entries with an unrelated RTP base → join must still find the bench viewer's frames (test in Task 7).
- `bench_seq` sent as negative, fractional or string → engine records nothing and still delivers the tap (test in Task 2).
- `bench.fetch` with `count` 0, huge, or `from` past the end → clamped, no crash, empty chunk at end (tests in Tasks 1 and 2).
- Bench page run against a host without `EMUCTRL_BENCH=1` → page fails fast with "engine bench mode off or not responding" instead of hanging (test in Task 6).

---

### Task 1: Engine bench log ring buffer

**Files:**
- Create: `engine/src/bench_log.h`
- Create: `engine/src/bench_log.cpp`
- Create: `engine/test/test_bench_log.cpp`
- Modify: `engine/CMakeLists.txt` (add sources to `engine_core` and `engine_tests`)

**Interfaces:**
- Consumes: nothing.
- Produces (namespace `bench`):
  - `enum class EntryKind : std::uint8_t { Input = 0, Frame = 1 };`
  - `struct Entry { EntryKind kind; std::uint64_t key; std::int64_t t1Us; std::int64_t t2Us; std::int64_t t3Us; };` — Input: key = `bench_seq`, t1 = received, t2 = injected, t3 = 0. Frame: key = RTP timestamp, t1 = read, t2 = packetized, t3 = sent.
  - `struct Chunk { std::uint64_t from; std::uint64_t total; std::size_t capacity; bool wrapped; std::vector<Entry> entries; };`
  - `constexpr std::size_t kCapacity = 65536;`
  - `void SetEnabled(bool)`, `bool Enabled()`, `std::int64_t NowUs()`, `void Reset()`, `void Record(const Entry&)`, `Chunk Fetch(std::uint64_t from, std::size_t maxCount)`, `void MarkFrameRead()`, `std::int64_t CurrentFrameReadUs()`.

- [ ] **Step 1: Write the failing tests**

`engine/test/test_bench_log.cpp`:

```cpp
#include <gtest/gtest.h>
#include "bench_log.h"

namespace {

class BenchLogTest : public ::testing::Test {
protected:
    void SetUp() override { bench::SetEnabled(true); }
    void TearDown() override { bench::SetEnabled(false); }
};

bench::Entry FrameEntry(std::uint64_t key) {
    return bench::Entry{bench::EntryKind::Frame, key, 1, 2, 3};
}

}  // namespace

TEST(BenchLog, RecordIsNoopWhenDisabled) {
    bench::SetEnabled(false);
    bench::Record(FrameEntry(1));
    EXPECT_EQ(bench::Fetch(0, 10).total, 0u);
}

TEST_F(BenchLogTest, FetchesRecordedEntriesInOrder) {
    bench::Record(FrameEntry(10));
    bench::Record(bench::Entry{bench::EntryKind::Input, 7, 100, 200, 0});

    const auto chunk = bench::Fetch(0, 10);
    EXPECT_EQ(chunk.total, 2u);
    EXPECT_EQ(chunk.from, 0u);
    EXPECT_EQ(chunk.capacity, bench::kCapacity);
    EXPECT_FALSE(chunk.wrapped);
    ASSERT_EQ(chunk.entries.size(), 2u);
    EXPECT_EQ(chunk.entries[0].kind, bench::EntryKind::Frame);
    EXPECT_EQ(chunk.entries[0].key, 10u);
    EXPECT_EQ(chunk.entries[1].kind, bench::EntryKind::Input);
    EXPECT_EQ(chunk.entries[1].t2Us, 200);
}

TEST_F(BenchLogTest, FetchHonoursFromAndMaxCount) {
    for (std::uint64_t i = 0; i < 5; ++i) bench::Record(FrameEntry(i));

    const auto chunk = bench::Fetch(2, 2);
    EXPECT_EQ(chunk.from, 2u);
    ASSERT_EQ(chunk.entries.size(), 2u);
    EXPECT_EQ(chunk.entries[0].key, 2u);
    EXPECT_EQ(chunk.entries[1].key, 3u);
}

TEST_F(BenchLogTest, FetchPastTheEndReturnsEmptyChunk) {
    bench::Record(FrameEntry(0));
    const auto chunk = bench::Fetch(5, 10);
    EXPECT_EQ(chunk.total, 1u);
    EXPECT_EQ(chunk.from, 1u);
    EXPECT_TRUE(chunk.entries.empty());
    EXPECT_FALSE(chunk.wrapped);
}

TEST_F(BenchLogTest, WraparoundKeepsNewestAndFlagsOlderFrom) {
    for (std::uint64_t i = 0; i < bench::kCapacity + 10; ++i) bench::Record(FrameEntry(i));

    const auto chunk = bench::Fetch(0, 3);
    EXPECT_TRUE(chunk.wrapped);
    EXPECT_EQ(chunk.from, 10u);
    ASSERT_EQ(chunk.entries.size(), 3u);
    EXPECT_EQ(chunk.entries[0].key, 10u);
    EXPECT_EQ(chunk.entries[2].key, 12u);
}

TEST_F(BenchLogTest, ResetDiscardsEntries) {
    bench::Record(FrameEntry(0));
    bench::Reset();
    EXPECT_EQ(bench::Fetch(0, 10).total, 0u);
}

TEST_F(BenchLogTest, MarkFrameReadStoresTimeOnlyWhenEnabled) {
    bench::MarkFrameRead();
    const auto marked = bench::CurrentFrameReadUs();
    EXPECT_GT(marked, 0);
    EXPECT_LE(marked, bench::NowUs());

    bench::SetEnabled(false);
    bench::MarkFrameRead();
    EXPECT_EQ(bench::CurrentFrameReadUs(), marked);
}
```

- [ ] **Step 2: Register files in CMake**

In `engine/CMakeLists.txt`, add `src/bench_log.cpp` to the `add_library(engine_core ...)` list (after `src/send_pacer.cpp`) and `test/test_bench_log.cpp` to `add_executable(engine_tests ...)` (after `test/test_send_pacer.cpp`).

- [ ] **Step 3: Run tests to verify they fail**

Windows: `cmake --build engine\build --config Release`
Expected: build FAILS — `bench_log.h` not found.

- [ ] **Step 4: Write the header**

`engine/src/bench_log.h`:

```cpp
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
```

- [ ] **Step 5: Write the implementation**

`engine/src/bench_log.cpp`:

```cpp
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
```

- [ ] **Step 6: Run tests to verify they pass**

Windows: `cmake --build engine\build --config Release; engine\build\Release\engine_tests.exe --gtest_filter=BenchLog*`
Expected: 7 tests PASS. (On the Mac: commit, push, confirm the `build-engine` CI job's `Run engine_tests` step passes.)

- [ ] **Step 7: Commit**

```bash
git add engine/src/bench_log.h engine/src/bench_log.cpp engine/test/test_bench_log.cpp engine/CMakeLists.txt
git commit -m "feat(engine): add a lab-only latency ring buffer"
```

---

### Task 2: Engine bench messages and input timing

**Files:**
- Create: `engine/src/bench_messages.h`
- Create: `engine/src/bench_messages.cpp`
- Create: `engine/test/test_bench_messages.cpp`
- Modify: `engine/src/input_router.cpp` (`HandleMessage`, around lines 101–140)
- Modify: `engine/src/main.cpp` (usage text and startup, around lines 63–80)
- Modify: `engine/test/test_input_router.cpp` (append tests)
- Modify: `engine/CMakeLists.txt`

**Interfaces:**
- Consumes: Task 1's `bench::Enabled/NowUs/Reset/Record/Fetch/Entry/EntryKind/kCapacity`.
- Produces (namespace `bench`):
  - `constexpr std::size_t kMaxFetchCount = 500;`
  - `bool IsBenchMessage(const std::string& type);` — true when `type` starts with `bench.`
  - `bool EnabledFromEnv(const char* value);` — true only for exactly `"1"`
  - `std::optional<std::string> HandleMessage(const nlohmann::json& msg);`
- Wire format (the bench page in Tasks 6 and 9 depends on this exactly):
  - `{"type":"bench.ping","id":<n>}` → `{"type":"bench.pong","id":<n>,"engine_us":<int>}`
  - `{"type":"bench.start"}` → clears the log → `{"type":"bench.started","engine_us":<int>}`
  - `{"type":"bench.fetch","from":<uint>,"count":<uint>}` → `{"type":"bench.chunk","from":<uint>,"total":<uint>,"capacity":65536,"wrapped":<bool>,"entries":[[kind,key,t1,t2,t3],...]}` where `kind` is `0` input / `1` frame. `count` clamps to 1..500, default 500; missing or non-unsigned `from` → 0.
  - `{"type":"click","x":0.5,"y":0.5,"bench_seq":<uint>}` → normal click, plus an Input entry when bench is on.
  - With bench off: no reply to any `bench.*` message.

- [ ] **Step 1: Write the failing message tests**

`engine/test/test_bench_messages.cpp`:

```cpp
#include <gtest/gtest.h>
#include "bench_log.h"
#include "bench_messages.h"
#include <nlohmann/json.hpp>

using json = nlohmann::json;

namespace {

class BenchMessagesTest : public ::testing::Test {
protected:
    void SetUp() override { bench::SetEnabled(true); }
    void TearDown() override { bench::SetEnabled(false); }
};

json Reply(const char* message) {
    auto reply = bench::HandleMessage(json::parse(message));
    EXPECT_TRUE(reply.has_value());
    return reply ? json::parse(*reply) : json{};
}

}  // namespace

TEST(BenchMessages, IgnoredWhenBenchIsOff) {
    bench::SetEnabled(false);
    EXPECT_FALSE(bench::HandleMessage(json::parse(R"({"type":"bench.ping","id":1})")));
    EXPECT_FALSE(bench::HandleMessage(json::parse(R"({"type":"bench.fetch"})")));
}

TEST(BenchMessages, RecognisesBenchPrefixOnly) {
    EXPECT_TRUE(bench::IsBenchMessage("bench.ping"));
    EXPECT_FALSE(bench::IsBenchMessage("click"));
    EXPECT_FALSE(bench::IsBenchMessage("benchmark"));
}

TEST(BenchMessages, EnabledFromEnvOnlyForExactlyOne) {
    EXPECT_TRUE(bench::EnabledFromEnv("1"));
    EXPECT_FALSE(bench::EnabledFromEnv("0"));
    EXPECT_FALSE(bench::EnabledFromEnv("true"));
    EXPECT_FALSE(bench::EnabledFromEnv(""));
    EXPECT_FALSE(bench::EnabledFromEnv(nullptr));
}

TEST_F(BenchMessagesTest, PingEchoesIdWithEngineTime) {
    const auto before = bench::NowUs();
    const auto reply = Reply(R"({"type":"bench.ping","id":42})");
    EXPECT_EQ(reply["type"], "bench.pong");
    EXPECT_EQ(reply["id"], 42);
    EXPECT_GE(reply["engine_us"].get<std::int64_t>(), before);
    EXPECT_LE(reply["engine_us"].get<std::int64_t>(), bench::NowUs());
}

TEST_F(BenchMessagesTest, StartClearsTheLog) {
    bench::Record(bench::Entry{bench::EntryKind::Frame, 1, 1, 2, 3});
    EXPECT_EQ(Reply(R"({"type":"bench.start"})")["type"], "bench.started");
    EXPECT_EQ(bench::Fetch(0, 10).total, 0u);
}

TEST_F(BenchMessagesTest, FetchReturnsCompactEntries) {
    bench::Record(bench::Entry{bench::EntryKind::Input, 7, 100, 200, 0});
    bench::Record(bench::Entry{bench::EntryKind::Frame, 9000, 300, 400, 500});

    const auto reply = Reply(R"({"type":"bench.fetch","from":0,"count":10})");
    EXPECT_EQ(reply["type"], "bench.chunk");
    EXPECT_EQ(reply["from"], 0);
    EXPECT_EQ(reply["total"], 2);
    EXPECT_EQ(reply["capacity"], bench::kCapacity);
    EXPECT_EQ(reply["wrapped"], false);
    EXPECT_EQ(reply["entries"], json::parse("[[0,7,100,200,0],[1,9000,300,400,500]]"));
}

TEST_F(BenchMessagesTest, FetchClampsCountAndDefaultsBadFields) {
    for (std::uint64_t i = 0; i < 3; ++i) {
        bench::Record(bench::Entry{bench::EntryKind::Frame, i, 1, 2, 3});
    }
    EXPECT_EQ(Reply(R"({"type":"bench.fetch","from":0,"count":0})")["entries"].size(), 1u);
    EXPECT_EQ(Reply(R"({"type":"bench.fetch","from":0,"count":100000})")["entries"].size(), 3u);
    EXPECT_EQ(Reply(R"({"type":"bench.fetch","from":-5,"count":1.5})")["entries"].size(), 3u);
    EXPECT_EQ(Reply(R"({"type":"bench.fetch","from":99})")["entries"].size(), 0u);
}

TEST_F(BenchMessagesTest, UnknownBenchOpGetsNoReply) {
    EXPECT_FALSE(bench::HandleMessage(json::parse(R"({"type":"bench.nope"})")));
}
```

- [ ] **Step 2: Write the failing input-router tests**

Append to `engine/test/test_input_router.cpp` (add `#include "bench_log.h"` to the includes at the top):

```cpp
TEST(InputRouter, BenchClickRecordsInputTiming) {
    bench::SetEnabled(true);
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());
    InputRouter router(source);

    router.HandleMessageForTest(R"({"type":"click","x":0.5,"y":0.5,"bench_seq":7})");

    const auto chunk = bench::Fetch(0, 10);
    ASSERT_EQ(chunk.entries.size(), 1u);
    EXPECT_EQ(chunk.entries[0].kind, bench::EntryKind::Input);
    EXPECT_EQ(chunk.entries[0].key, 7u);
    EXPECT_GT(chunk.entries[0].t1Us, 0);
    EXPECT_GE(chunk.entries[0].t2Us, chunk.entries[0].t1Us);
    bench::SetEnabled(false);
}

TEST(InputRouter, BenchClickWithInvalidSeqStillTapsButRecordsNothing) {
    bench::SetEnabled(true);
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());
    InputRouter router(source);

    router.HandleMessageForTest(R"({"type":"click","x":0.5,"y":0.5,"bench_seq":-1})");
    router.HandleMessageForTest(R"({"type":"click","x":0.5,"y":0.5,"bench_seq":1.5})");
    router.HandleMessageForTest(R"({"type":"click","x":0.5,"y":0.5,"bench_seq":"7"})");
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    EXPECT_EQ(bench::Fetch(0, 10).total, 0u);
    EXPECT_EQ(fake.ControlBytesReceived(), 3u * 2u * 32u);  // DOWN + UP per click
    bench::SetEnabled(false);
}

TEST(InputRouter, ClickRecordsNothingWhenBenchIsOff) {
    bench::SetEnabled(false);
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());
    InputRouter router(source);

    router.HandleMessageForTest(R"({"type":"click","x":0.5,"y":0.5,"bench_seq":7})");
    EXPECT_EQ(bench::Fetch(0, 10).total, 0u);
}
```

Note: the 32-byte touch-event size comes from `DecodeTouchEvents` at the top of this test file.

- [ ] **Step 3: Register files in CMake**

Add `src/bench_messages.cpp` to `engine_core` and `test/test_bench_messages.cpp` to `engine_tests` in `engine/CMakeLists.txt`.

- [ ] **Step 4: Run tests to verify they fail**

Windows: `cmake --build engine\build --config Release`
Expected: build FAILS — `bench_messages.h` not found.

- [ ] **Step 5: Write the message handler**

`engine/src/bench_messages.h`:

```cpp
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
```

`engine/src/bench_messages.cpp`:

```cpp
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
```

- [ ] **Step 6: Wire it into InputRouter**

In `engine/src/input_router.cpp` add includes after `#include "peer_registry.h"`:

```cpp
#include "bench_log.h"
#include "bench_messages.h"
```

Replace the start of `InputRouter::HandleMessage` (from the function signature through the `echo` branch) with:

```cpp
void InputRouter::HandleMessage(PeerSession* peer, const std::string& jsonMessage) {
    const std::int64_t receivedUs = bench::Enabled() ? bench::NowUs() : 0;
    auto msg = json::parse(jsonMessage, nullptr, false);
    if (msg.is_discarded()) return;
    std::string type = msg.value("type", "");

    if (type == "echo") {
        if (peer) peer->SendInputMessage(jsonMessage);
        return;
    }

    // Answered before the control check: bench messages never touch scrcpy.
    if (bench::IsBenchMessage(type)) {
        auto reply = bench::HandleMessage(msg);
        if (reply && peer) peer->SendInputMessage(*reply);
        return;
    }
```

In the `if (type == "click")` branch, after the second `control->SendTouch(... ACTION_UP ...)` call, add:

```cpp
        if (receivedUs != 0) {
            auto seq = msg.find("bench_seq");
            if (seq != msg.end() && seq->is_number_unsigned()) {
                bench::Record(bench::Entry{bench::EntryKind::Input, seq->get<std::uint64_t>(),
                                           receivedUs, bench::NowUs(), 0});
            }
        }
```

- [ ] **Step 7: Turn bench mode on from the environment**

In `engine/src/main.cpp` add `#include "bench_log.h"` and `#include "bench_messages.h"` with the other includes. Add `EMUCTRL_BENCH` to the usage text:

```cpp
                     "Environment: ENGINE_WHEP_CAPABILITY_SECRET, "
                     "ENGINE_LOCAL_ICE_SERVERS, ENGINE_VIDEO_BIT_RATE, EMUCTRL_BENCH\n";
```

Right after the `ENGINE_VIDEO_BIT_RATE` block (before `PeerRegistry registry;`):

```cpp
        // Lab latency bench (scripts/bench). Off unless exactly "1".
        if (bench::EnabledFromEnv(GetEnvOrEmpty("EMUCTRL_BENCH").c_str())) {
            bench::SetEnabled(true);
            std::cerr << "[engine] bench mode on" << std::endl;
        }
```

- [ ] **Step 8: Run tests to verify they pass**

Windows: `cmake --build engine\build --config Release; engine\build\Release\engine_tests.exe --gtest_filter=Bench*:InputRouter.*`
Expected: all PASS, including the existing InputRouter tests.

- [ ] **Step 9: Commit**

```bash
git add engine/src/bench_messages.h engine/src/bench_messages.cpp engine/src/input_router.cpp engine/src/main.cpp engine/test/test_bench_messages.cpp engine/test/test_input_router.cpp engine/CMakeLists.txt
git commit -m "feat(engine): answer bench messages and time bench taps"
```

---

### Task 3: Engine per-frame timing

**Files:**
- Modify: `engine/src/scrcpy_source.cpp` (`ScrcpySource::FanOut`, line ~164)
- Modify: `engine/src/peer_session.cpp` (`PeerSession::SendVideoNalu`, line ~196)
- Modify: `engine/test/test_peer_session.cpp` (append a test)

**Interfaces:**
- Consumes: Task 1's `bench::MarkFrameRead`, `CurrentFrameReadUs`, `Record`, `Fetch`, `NowUs`, `Enabled`.
- Produces: one `EntryKind::Frame` entry per access unit per peer, with key = the 32-bit RTP timestamp that peer sends (the browser sees the same value as `requestVideoFrameCallback` `metadata.rtpTimestamp`).

Why it works: `ScrcpyVideoClient`'s read thread calls `FanOut` → `SourceVideoFanout::SendAccessUnit` → each peer's `SendVideoNalu` → `track->send` → packetizer chain → `PacedSendHandler`, all synchronously on that one thread. So the thread_local read time is the current frame's, and the time after `send()` returns is when the pacer released the last packet.

- [ ] **Step 1: Write the failing test**

Append to `engine/test/test_peer_session.cpp` (add `#include "bench_log.h"` and `#include <cstdint>` at the top):

```cpp
TEST(PeerSession, BenchModeRecordsFrameTimingKeyedByRtpTimestamp) {
    bench::SetEnabled(true);
    rtc::PeerConnection viewerPc(ManualNegotiationConfig());
    OfferChannels channels;
    std::string offer = CreateGatheredOffer(viewerPc, channels);

    PeerSession session("bench-peer", {});
    viewerPc.setRemoteDescription(rtc::Description(session.AnswerOffer(offer), "answer"));

    // The track opens shortly after the connection; keep sending a tiny IDR
    // NAL until one frame is recorded.
    const std::uint8_t idr[] = {0, 0, 0, 1, 0x65, 0x88, 0x84, 0x00};
    for (int i = 0; i < 200 && bench::Fetch(0, 1).total == 0; ++i) {
        session.SendVideoNalu(idr, sizeof(idr));
        std::this_thread::sleep_for(std::chrono::milliseconds(25));
    }

    const auto chunk = bench::Fetch(0, 1);
    ASSERT_EQ(chunk.entries.size(), 1u);
    const auto& frame = chunk.entries[0];
    EXPECT_EQ(frame.kind, bench::EntryKind::Frame);
    EXPECT_LE(frame.key, 0xFFFFFFFFull);
    EXPECT_GT(frame.t2Us, 0);
    EXPECT_LE(frame.t1Us, frame.t2Us);  // no FanOut here: read falls back to packetized
    EXPECT_GE(frame.t3Us, frame.t2Us);

    session.Close();
    bench::SetEnabled(false);
}
```

- [ ] **Step 2: Run test to verify it fails**

Windows: `cmake --build engine\build --config Release; engine\build\Release\engine_tests.exe --gtest_filter=PeerSession.BenchMode*`
Expected: FAIL — `chunk.entries.size()` is 0.

- [ ] **Step 3: Mark the read time in FanOut**

`engine/src/scrcpy_source.cpp`: add `#include "bench_log.h"` and change `FanOut` to:

```cpp
void ScrcpySource::FanOut(const std::uint8_t* data, size_t size) {
    bench::MarkFrameRead();
    lastFrameAt_.store(std::chrono::steady_clock::now());
    videoFanout_.SendAccessUnit(data, size);
}
```

- [ ] **Step 4: Record the frame in SendVideoNalu**

`engine/src/peer_session.cpp`: add `#include "bench_log.h"` and replace the tail of `PeerSession::SendVideoNalu` (from `auto elapsed = ...` to the end of the function) with:

```cpp
    auto elapsed = std::chrono::steady_clock::now() - impl_->streamStart;
    double elapsedSeconds = std::chrono::duration<double>(elapsed).count();
    rtpConfig->timestamp = rtpConfig->startTimestamp +
        rtpConfig->secondsToTimestamp(elapsedSeconds);
    const std::uint32_t rtpTimestamp = rtpConfig->timestamp;

    const bool benching = bench::Enabled();
    const std::int64_t packetizedUs = benching ? bench::NowUs() : 0;

    // Synchronous through the packetizer and PacedSendHandler, so this
    // returns once the pacer has released the frame's last packet.
    videoTrack->send(reinterpret_cast<const std::byte*>(data), size);

    if (benching) {
        const std::int64_t readUs = bench::CurrentFrameReadUs();
        bench::Record(bench::Entry{bench::EntryKind::Frame, rtpTimestamp,
                                   readUs != 0 ? readUs : packetizedUs,
                                   packetizedUs, bench::NowUs()});
    }
}
```

- [ ] **Step 5: Run tests to verify they pass**

Windows: `cmake --build engine\build --config Release; engine\build\Release\engine_tests.exe`
Expected: whole suite PASS.

- [ ] **Step 6: Commit**

```bash
git add engine/src/scrcpy_source.cpp engine/src/peer_session.cpp engine/test/test_peer_session.cpp
git commit -m "feat(engine): time each frame from read to paced send in bench mode"
```

---

### Task 4: Host bench mode (target page and routes)

**Files:**
- Create: `src/server/bench/target.html`
- Create: `tests/test_bench_mode.py`
- Modify: `src/config.py` (add `bench_mode_from_env`, `BENCH_MODE`)
- Modify: `src/server/app.py` (`_PAIRING_EXEMPT_PATHS` line ~31, `create_app` signature line ~319, page routes near `/stream` line ~398, `web_rsc_payload` line ~429)
- Modify: `src/main.py` (line ~194)

**Interfaces:**
- Consumes: nothing from earlier tasks (the engine reads the same `EMUCTRL_BENCH` it inherits).
- Produces:
  - `config.bench_mode_from_env(environ: Mapping[str, str]) -> bool`, `config.BENCH_MODE: bool`
  - `create_app(instance_manager, pairing=None, bench: bool = False)`
  - `GET /bench/target.html` (static target), `GET /bench` (web shell `bench.html`), `GET /bench.txt` — bench mode only; all pairing-exempt.

- [ ] **Step 1: Write the failing tests**

`tests/test_bench_mode.py`:

```python
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from config import bench_mode_from_env
from server.app import create_app
from server.pairing import PairingStore

LOOPBACK = ("127.0.0.1", 5000)
LAN = ("192.168.1.50", 5000)
GUEST_HEADERS = {"Host": "10.0.2.2:8080"}  # LDPlayer guest reaching the host


def _make(peer, bench):
    manager = MagicMock()
    manager.list_instances.return_value = []
    manager.active = None
    with patch("server.app.get_best_ip", return_value="127.0.0.1"):
        app = create_app(manager, PairingStore(), bench=bench)
    return TestClient(app, client=peer)


@pytest.fixture
def web_dir(tmp_path):
    (tmp_path / "bench.html").write_text("<html>bench shell</html>", encoding="utf-8")
    (tmp_path / "bench.txt").write_text("rsc", encoding="utf-8")
    with patch("server.app.WEB_BUILD_DIR", str(tmp_path)):
        yield tmp_path


@pytest.mark.parametrize("value,expected", [
    ("1", True), ("0", False), ("true", False), ("", False), (None, False),
])
def test_bench_mode_only_for_exactly_one(value, expected):
    environ = {} if value is None else {"EMUCTRL_BENCH": value}
    assert bench_mode_from_env(environ) is expected


@pytest.mark.parametrize("path", ["/bench", "/bench.txt", "/bench/target.html"])
def test_bench_routes_absent_without_bench_mode(web_dir, path):
    client = _make(LOOPBACK, bench=False)
    assert client.get(path, headers=GUEST_HEADERS).status_code == 404


def test_target_page_served_to_guest_without_token():
    client = _make(LOOPBACK, bench=True)
    response = client.get("/bench/target.html", headers=GUEST_HEADERS)
    assert response.status_code == 200
    assert "pointerdown" in response.text
    assert response.headers["cache-control"] == "no-store"


def test_target_page_is_static_for_unpaired_lan_peer():
    client = _make(LAN, bench=True)
    assert client.get("/bench/target.html").status_code == 200


def test_bench_shell_served_in_bench_mode(web_dir):
    client = _make(LAN, bench=True)
    assert client.get("/bench").text == "<html>bench shell</html>"
    assert client.get("/bench.txt").status_code == 200


def test_bench_mode_keeps_api_routes_paired(web_dir):
    client = _make(LAN, bench=True)
    assert client.get("/instances").status_code == 401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_bench_mode.py -v`
Expected: FAIL — `ImportError: cannot import name 'bench_mode_from_env'`.

- [ ] **Step 3: Add the config switch**

`src/config.py`, after `ENGINE_LOCAL_ICE_SERVERS`:

```python
def bench_mode_from_env(environ) -> bool:
    """Lab latency bench (scripts/bench); on only when EMUCTRL_BENCH is exactly "1"."""
    return environ.get("EMUCTRL_BENCH") == "1"


BENCH_MODE = bench_mode_from_env(os.environ)
```

- [ ] **Step 4: Add the target page**

`src/server/bench/target.html`:

```html
<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>Bench target</title>
<style>
  html, body { margin: 0; height: 100%; background: #000; overflow: hidden; touch-action: none; }
</style>
</head>
<body>
<script>
  // Each tap flips the whole screen black <-> white; the bench page times
  // the flip. Nothing else may be drawn.
  let white = false;
  document.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    white = !white;
    document.body.style.background = white ? "#fff" : "#000";
    if (!document.fullscreenElement && document.documentElement.requestFullscreen) {
      document.documentElement.requestFullscreen().catch(() => {});
    }
  }, { passive: false });
</script>
</body>
</html>
```

- [ ] **Step 5: Add the routes**

`src/server/app.py`:

1. Add the three paths to `_PAIRING_EXEMPT_PATHS`:

```python
_PAIRING_EXEMPT_PATHS = {
    "/", "/pair", "/pair/status", "/stream",
    "/index.txt", "/pair.txt", "/stream.txt", "/instances.txt", "/account.txt",
    "/manifest.json", "/icon-192.png", "/icon-512.png", "/favicon.ico", "/404.html",
    # Lab bench (registered only in bench mode): static shell and target page.
    "/bench", "/bench.txt", "/bench/target.html",
}
```

2. Change the signature:

```python
def create_app(instance_manager: InstanceManager,
               pairing: PairingStore | None = None,
               bench: bool = False) -> FastAPI:
```

3. After the `@app.get("/stream")` route, add:

```python
    if bench:
        bench_target = Path(__file__).parent / "bench" / "target.html"

        @app.get("/bench")
        async def bench_page():
            return _serve_web_page("bench.html")

        @app.get("/bench/target.html")
        async def bench_target_page():
            return HTMLResponse(
                bench_target.read_text(encoding="utf-8"),
                headers={"Cache-Control": "no-store"},
            )
```

4. In `web_rsc_payload`, right after the `_RSC_PAYLOAD_NAME` check:

```python
        if page == "bench" and not bench:
            raise HTTPException(status_code=404, detail="Not found")
```

5. `src/main.py`: import `BENCH_MODE` from `config` alongside the existing config imports, and change the call to:

```python
    fastapi_app = create_app(instance_manager, pairing=pairing, bench=BENCH_MODE)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_bench_mode.py tests/test_app.py tests/test_app_pairing.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/config.py src/server/app.py src/server/bench/target.html src/main.py tests/test_bench_mode.py
git commit -m "feat(host): serve the bench target and page only in bench mode"
```

---

### Task 5: Expose DataChannel messages from `connectWhep`

**Files:**
- Modify: `packages/core/src/webrtc/whep.ts` (`ConnectWhepOpts` ~line 52; channel `message` listener ~line 166)
- Modify: `packages/core/src/webrtc/whep.test.ts` (append a test)

**Interfaces:**
- Consumes: nothing.
- Produces: `ConnectWhepOpts.onMessage?: (message: any) => void` — called with every parsed JSON message on the `input` channel (including `echo`), after the existing echo handling.

- [ ] **Step 1: Write the failing test**

Append to `packages/core/src/webrtc/whep.test.ts`:

```ts
test("passes every parsed input-channel message to onMessage", async () => {
  const pc = fakePc();
  const fetchImpl = jest.fn(async () => whepResponse()) as any;
  const messages: any[] = [];
  const promise = connectWhep({
    whepUrl: "http://host/whep",
    whepToken: "tok",
    iceServers: [],
    onStream: () => {},
    onInputRtt: () => {},
    onState: () => {},
    onMessage: (message) => messages.push(message),
    RTCImpl: function () { return pc; } as any,
    fetchImpl,
  });
  await Promise.resolve();
  await Promise.resolve();
  fireReady(pc);
  await promise;

  pc.dc._fire("message", { data: JSON.stringify({ type: "bench.pong", id: 1, engine_us: 5 }) });
  pc.dc._fire("message", { data: "not json" });

  expect(messages).toEqual([{ type: "bench.pong", id: 1, engine_us: 5 }]);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test:core -- -t "onMessage"`
Expected: FAIL — TypeScript error, `onMessage` does not exist in type `ConnectWhepOpts`.

- [ ] **Step 3: Implement**

In `ConnectWhepOpts` add after `onState`:

```ts
  // Every parsed JSON message on the input channel (used by the lab bench).
  onMessage?: (message: any) => void;
```

Replace the channel `message` listener body with:

```ts
  listen(channel, "message", (event: any) => {
    if (closed) return;
    let msg: any;
    try {
      msg = JSON.parse(event.data);
    } catch {
      return; // ignore malformed messages
    }
    if (msg && msg.type === "echo" && typeof msg.t === "number") {
      opts.onInputRtt(Date.now() - msg.t);
    }
    try { opts.onMessage?.(msg); } catch {}
  });
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npm run test:core`
Expected: all PASS (existing echo/RTT tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add packages/core/src/webrtc/whep.ts packages/core/src/webrtc/whep.test.ts
git commit -m "feat(core): surface input-channel messages from the WHEP session"
```

---

### Task 6: Bench clock sync and engine client

**Files:**
- Create: `packages/core/src/bench/clock.ts`
- Create: `packages/core/src/bench/clock.test.ts`
- Create: `packages/core/src/bench/benchClient.ts`
- Create: `packages/core/src/bench/benchClient.test.ts`
- Create: `packages/core/src/bench/index.ts`
- Modify: `packages/core/src/index.ts` (add `export * from "./bench";`)

**Interfaces:**
- Consumes: Task 2's wire format.
- Produces:
  - `type PingSample = { sendMs: number; recvMs: number; engineUs: number }`
  - `type ClockEstimate = { offsetUs: number; rttMs: number; samples: number }`
  - `estimateOffset(samples: PingSample[]): ClockEstimate` — min-RTT sample; `offsetUs = engineUs − ((sendMs + recvMs) / 2) × 1000`; throws on empty input
  - `engineToLocalMs(engineUs: number, offsetUs: number): number` — `(engineUs − offsetUs) / 1000`
  - `clockDriftUs(before: ClockEstimate, after: ClockEstimate): number`
  - `type EngineEntry = { kind: "input" | "frame"; key: number; t1Us: number; t2Us: number; t3Us: number }`
  - `type BenchChannel = { send(message: { type: string; [k: string]: unknown }): void; subscribe(handler: (message: any) => void): () => void }`
  - `class BenchTimeoutError extends Error` — message `"engine bench mode off or not responding"`
  - `makeBenchClient(channel: BenchChannel, opts: { now: () => number; timeoutMs?: number })` → `{ ping(): Promise<PingSample>; syncClock(count?: number): Promise<ClockEstimate>; start(): Promise<void>; fetchAll(): Promise<{ entries: EngineEntry[]; wrapped: boolean }> }` (default `timeoutMs` 2000, `syncClock` default count 50)

- [ ] **Step 1: Write the failing clock tests**

`packages/core/src/bench/clock.test.ts`:

```ts
import { clockDriftUs, engineToLocalMs, estimateOffset } from "./clock";

test("uses the lowest-RTT sample and its midpoint", () => {
  const estimate = estimateOffset([
    { sendMs: 0, recvMs: 20, engineUs: 5_000_000 },   // rtt 20
    { sendMs: 100, recvMs: 102, engineUs: 5_101_000 }, // rtt 2, midpoint 101 ms
    { sendMs: 200, recvMs: 210, engineUs: 5_300_000 }, // rtt 10
  ]);
  expect(estimate).toEqual({ offsetUs: 5_101_000 - 101_000, rttMs: 2, samples: 3 });
});

test("converts engine time back to the receiver clock", () => {
  const { offsetUs } = estimateOffset([{ sendMs: 10, recvMs: 12, engineUs: 9_011_000 }]);
  expect(engineToLocalMs(9_011_000, offsetUs)).toBeCloseTo(11, 6);
  expect(engineToLocalMs(9_021_000, offsetUs)).toBeCloseTo(21, 6);
});

test("drift is the absolute offset change", () => {
  const a = { offsetUs: 1_000, rttMs: 1, samples: 1 };
  const b = { offsetUs: -1_500, rttMs: 1, samples: 1 };
  expect(clockDriftUs(a, b)).toBe(2_500);
});

test("refuses to estimate without samples", () => {
  expect(() => estimateOffset([])).toThrow("no clock samples");
});
```

- [ ] **Step 2: Write the failing client tests**

`packages/core/src/bench/benchClient.test.ts`:

```ts
import { BenchTimeoutError, makeBenchClient } from "./benchClient";
import type { BenchChannel } from "./benchClient";

// Answers like engine/src/bench_messages.cpp.
function fakeEngine(entries: number[][], opts: { engineUs?: number; silent?: boolean } = {}) {
  const handlers = new Set<(m: any) => void>();
  const sent: any[] = [];
  const reply = (m: any) => queueMicrotask(() => handlers.forEach((h) => h(m)));
  const channel: BenchChannel = {
    subscribe(handler) { handlers.add(handler); return () => handlers.delete(handler); },
    send(message: any) {
      sent.push(message);
      if (opts.silent) return;
      if (message.type === "bench.ping") reply({ type: "bench.pong", id: message.id, engine_us: opts.engineUs ?? 7_000 });
      if (message.type === "bench.start") reply({ type: "bench.started", engine_us: 1 });
      if (message.type === "bench.fetch") {
        const from = message.from, count = Math.min(message.count, 2);
        reply({ type: "bench.chunk", from, total: entries.length, capacity: 65536, wrapped: false,
                entries: entries.slice(from, from + count) });
      }
    },
  };
  return { channel, sent };
}

test("ping pairs the reply by id and timestamps both ends", async () => {
  let now = 100;
  const { channel } = fakeEngine([]);
  const client = makeBenchClient(channel, { now: () => now++ });
  await expect(client.ping()).resolves.toEqual({ sendMs: 100, recvMs: 101, engineUs: 7_000 });
});

test("syncClock sends the requested number of pings", async () => {
  const { channel, sent } = fakeEngine([]);
  let now = 0;
  const client = makeBenchClient(channel, { now: () => (now += 1) });
  const estimate = await client.syncClock(5);
  expect(sent.filter((m) => m.type === "bench.ping")).toHaveLength(5);
  expect(estimate.samples).toBe(5);
});

test("fetchAll pages through the log and decodes compact entries", async () => {
  const { channel, sent } = fakeEngine([[0, 7, 1, 2, 0], [1, 900, 3, 4, 5], [1, 901, 6, 7, 8]]);
  const client = makeBenchClient(channel, { now: () => 0 });
  const result = await client.fetchAll();
  expect(sent.filter((m) => m.type === "bench.fetch").map((m) => m.from)).toEqual([0, 2]);
  expect(result.wrapped).toBe(false);
  expect(result.entries).toEqual([
    { kind: "input", key: 7, t1Us: 1, t2Us: 2, t3Us: 0 },
    { kind: "frame", key: 900, t1Us: 3, t2Us: 4, t3Us: 5 },
    { kind: "frame", key: 901, t1Us: 6, t2Us: 7, t3Us: 8 },
  ]);
});

test("a silent engine fails fast instead of hanging", async () => {
  jest.useFakeTimers();
  try {
    const { channel } = fakeEngine([], { silent: true });
    const client = makeBenchClient(channel, { now: () => 0, timeoutMs: 500 });
    const pending = client.ping();
    jest.advanceTimersByTime(500);
    await expect(pending).rejects.toBeInstanceOf(BenchTimeoutError);
    await expect(pending).rejects.toThrow("engine bench mode off or not responding");
  } finally {
    jest.useRealTimers();
  }
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `npm run test:core -- bench`
Expected: FAIL — cannot find module `./clock` / `./benchClient`.

- [ ] **Step 4: Implement clock.ts**

`packages/core/src/bench/clock.ts`:

```ts
export type PingSample = { sendMs: number; recvMs: number; engineUs: number };
export type ClockEstimate = { offsetUs: number; rttMs: number; samples: number };

// The lowest-RTT ping has the least room for asymmetric delay, so its
// midpoint is the best estimate of when the engine read its clock.
export function estimateOffset(samples: PingSample[]): ClockEstimate {
  if (samples.length === 0) throw new Error("no clock samples");
  let best = samples[0];
  for (const sample of samples) {
    if (sample.recvMs - sample.sendMs < best.recvMs - best.sendMs) best = sample;
  }
  const midpointUs = ((best.sendMs + best.recvMs) / 2) * 1000;
  return { offsetUs: best.engineUs - midpointUs, rttMs: best.recvMs - best.sendMs, samples: samples.length };
}

export function engineToLocalMs(engineUs: number, offsetUs: number): number {
  return (engineUs - offsetUs) / 1000;
}

export function clockDriftUs(before: ClockEstimate, after: ClockEstimate): number {
  return Math.abs(after.offsetUs - before.offsetUs);
}
```

- [ ] **Step 5: Implement benchClient.ts**

`packages/core/src/bench/benchClient.ts`:

```ts
import { estimateOffset } from "./clock";
import type { ClockEstimate, PingSample } from "./clock";

export type EngineEntry = { kind: "input" | "frame"; key: number; t1Us: number; t2Us: number; t3Us: number };

export type BenchChannel = {
  send(message: { type: string; [key: string]: unknown }): void;
  subscribe(handler: (message: any) => void): () => void;
};

export class BenchTimeoutError extends Error {
  constructor() {
    super("engine bench mode off or not responding");
  }
}

const FETCH_COUNT = 500;

export function makeBenchClient(channel: BenchChannel, opts: { now: () => number; timeoutMs?: number }) {
  const timeoutMs = opts.timeoutMs ?? 2000;
  let nextId = 1;

  function request(message: { type: string; [key: string]: unknown }, matches: (reply: any) => boolean): Promise<any> {
    return new Promise((resolve, reject) => {
      const unsubscribe = channel.subscribe((reply) => {
        if (!matches(reply)) return;
        clearTimeout(timer);
        unsubscribe();
        resolve(reply);
      });
      const timer = setTimeout(() => {
        unsubscribe();
        reject(new BenchTimeoutError());
      }, timeoutMs);
      channel.send(message);
    });
  }

  async function ping(): Promise<PingSample> {
    const id = nextId++;
    const sendMs = opts.now();
    const reply = await request({ type: "bench.ping", id }, (m) => m?.type === "bench.pong" && m.id === id);
    return { sendMs, recvMs: opts.now(), engineUs: reply.engine_us };
  }

  async function syncClock(count = 50): Promise<ClockEstimate> {
    const samples: PingSample[] = [];
    for (let i = 0; i < count; i++) samples.push(await ping());
    return estimateOffset(samples);
  }

  async function start(): Promise<void> {
    await request({ type: "bench.start" }, (m) => m?.type === "bench.started");
  }

  async function fetchAll(): Promise<{ entries: EngineEntry[]; wrapped: boolean }> {
    const entries: EngineEntry[] = [];
    let wrapped = false;
    let from = 0;
    for (;;) {
      const chunk = await request({ type: "bench.fetch", from, count: FETCH_COUNT }, (m) => m?.type === "bench.chunk");
      wrapped = wrapped || chunk.wrapped === true;
      for (const [kind, key, t1Us, t2Us, t3Us] of chunk.entries as number[][]) {
        entries.push({ kind: kind === 0 ? "input" : "frame", key, t1Us, t2Us, t3Us });
      }
      from = chunk.from + chunk.entries.length;
      if (chunk.entries.length === 0 || from >= chunk.total) break;
    }
    return { entries, wrapped };
  }

  return { ping, syncClock, start, fetchAll };
}
```

- [ ] **Step 6: Add the barrel exports**

`packages/core/src/bench/index.ts`:

```ts
export * from "./clock";
export * from "./benchClient";
```

`packages/core/src/index.ts`: append `export * from "./bench";`.

- [ ] **Step 7: Run tests to verify they pass**

Run: `npm run test:core`
Expected: all PASS.

- [ ] **Step 8: Commit**

```bash
git add packages/core/src/bench packages/core/src/index.ts
git commit -m "feat(core): add bench clock sync and engine log client"
```

---

### Task 7: Bench tap loop, flip detection and analysis

**Files:**
- Create: `packages/core/src/bench/detect.ts`
- Create: `packages/core/src/bench/taps.ts`
- Create: `packages/core/src/bench/taps.test.ts`
- Create: `packages/core/src/bench/analyze.ts`
- Create: `packages/core/src/bench/analyze.test.ts`
- Create: `packages/core/src/bench/transport.ts`
- Create: `packages/core/src/bench/transport.test.ts`
- Modify: `packages/core/src/bench/index.ts`

**Interfaces:**
- Consumes: Task 6's `engineToLocalMs`, `EngineEntry`.
- Produces:
  - `detect.ts`: `type TargetColor = "black" | "white"`; `WHITE_MIN = 160`; `BLACK_MAX = 96`; `centerLuminance(rgba: ArrayLike<number>, width: number, height: number): number`; `classify(lum: number): TargetColor | null`; `opposite(color: TargetColor): TargetColor`
  - `taps.ts`: `type FrameSample = { lum: number; rtpTimestamp: number | null; receiveTime: number | null; expectedDisplayTime: number }`; `type TapRecord = { seq: number; t0: number; t1: number | null; rtpTimestamp: number | null; receiveTime: number | null; miss: boolean }`; `type TapDeps = { sendClick(seq: number): void; nextFrame(timeoutMs: number): Promise<FrameSample | null>; now(): number; sleep(ms: number): Promise<void>; random(): number }`; `type TapOptions = { count; warmup; missTimeoutMs; gapMinMs; gapMaxMs; stableMs }` (all `number`); `DEFAULT_TAP_OPTIONS`; `class TargetNotInFrontError`; `runTaps(deps: TapDeps, options?: TapOptions): Promise<TapRecord[]>` (returns only non-warm-up taps)
  - `analyze.ts`: `STAGES`, `type StageName`, `type StageStats = { count; mean; p50; p95; p99; p50CiHalf }`, `type FrameMeta = { rtpTimestamp: number; receiveTime: number; expectedDisplayTime: number }`, `type AnalyzeInput = { taps: TapRecord[]; frames: FrameMeta[]; engine: EngineEntry[]; engineWrapped: boolean; offsetUs: number; driftUs: number; transportChanged: boolean }`, `type Validity = { failed: boolean; reasons: string[]; crossClockReliable: boolean; breakdownAvailable: boolean }`, `type Analysis = { stats: Partial<Record<StageName, StageStats>>; validity: Validity; missRate: number }`, `percentile(sorted, p)`, `bootstrapP50CiHalf(values, random, iterations?)`, `analyze(input, random?)`
  - `transport.ts`: `type SelectedPair = { id: string | null; remoteType: string | null }`; `selectedCandidatePair(stats: { forEach(cb: (report: any) => void): void }): SelectedPair`

- [ ] **Step 1: Write the failing tap-loop tests**

`packages/core/src/bench/taps.test.ts`:

```ts
import { centerLuminance, classify } from "./detect";
import { runTaps, TargetNotInFrontError } from "./taps";
import type { FrameSample, TapDeps } from "./taps";

const FRAME_MS = 16;
const OPTIONS = { count: 5, warmup: 2, missTimeoutMs: 2000, gapMinMs: 300, gapMaxMs: 700, stableMs: 1000 };

// A screen that flips `latencyMs` after each delivered click, sampled every 16 ms.
function fakeTarget({ latencyMs, dropSeq = -1, lum }: { latencyMs: number; dropSeq?: number; lum?: number }) {
  let now = 0;
  const flips: number[] = [];
  const colorAt = (t: number) => (flips.filter((f) => f + latencyMs <= t).length % 2 === 0 ? 0 : 255);
  const deps: TapDeps = {
    now: () => now,
    random: () => 0.5,
    sleep: async (ms) => { now += ms; },
    sendClick: (seq) => { if (seq !== dropSeq) flips.push(now); },
    nextFrame: async (timeoutMs): Promise<FrameSample | null> => {
      if (FRAME_MS > timeoutMs) { now += timeoutMs; return null; }
      now += FRAME_MS;
      return { lum: lum ?? colorAt(now), rtpTimestamp: now * 90, receiveTime: now - 5, expectedDisplayTime: now };
    },
  };
  return deps;
}

test("measures click-to-flip latency and drops warm-up taps", async () => {
  const records = await runTaps(fakeTarget({ latencyMs: 80 }), OPTIONS);
  expect(records.map((r) => r.seq)).toEqual([2, 3, 4, 5, 6]);
  for (const r of records) {
    expect(r.miss).toBe(false);
    expect(r.t1! - r.t0).toBeGreaterThanOrEqual(80);
    expect(r.t1! - r.t0).toBeLessThan(80 + FRAME_MS);
    expect(r.rtpTimestamp).toBe(r.t1! * 90);
  }
});

test("a lost click is a miss and later taps still measure", async () => {
  const records = await runTaps(fakeTarget({ latencyMs: 50, dropSeq: 3 }), OPTIONS);
  expect(records.find((r) => r.seq === 3)!.miss).toBe(true);
  expect(records.filter((r) => r.seq !== 3).every((r) => !r.miss)).toBe(true);
});

test("refuses to run when the target is not in front", async () => {
  await expect(runTaps(fakeTarget({ latencyMs: 50, lum: 128 }), OPTIONS)).rejects.toBeInstanceOf(TargetNotInFrontError);
});

test("centre luminance ignores the border and classify applies thresholds", () => {
  const width = 8, height = 8;
  const rgba = new Uint8ClampedArray(width * height * 4);
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) {
    const inside = x >= 2 && x < 6 && y >= 2 && y < 6;
    rgba.fill(inside ? 255 : 0, (y * width + x) * 4, (y * width + x) * 4 + 3);
  }
  expect(centerLuminance(rgba, width, height)).toBeCloseTo(255, 0);
  expect(classify(200)).toBe("white");
  expect(classify(40)).toBe("black");
  expect(classify(128)).toBeNull();
});
```

- [ ] **Step 2: Write the failing analysis and transport tests**

`packages/core/src/bench/analyze.test.ts`:

```ts
import { analyze, bootstrapP50CiHalf, percentile } from "./analyze";
import type { AnalyzeInput } from "./analyze";
import type { EngineEntry } from "./benchClient";
import type { TapRecord } from "./taps";

const OFFSET_US = 1_000_000_000; // engine clock = local ms * 1000 + OFFSET_US
const e = (localMs: number) => localMs * 1000 + OFFSET_US;
const fixedRandom = () => 0.5;

// One tap: t0=100, engine recv 104, inject 105, frame read 150, packetized 151,
// sent 153, receiveTime 160, displayed 175.
function tap(seq: number, shift = 0): { tap: TapRecord; engine: EngineEntry[] } {
  const s = shift;
  const rtp = 9000 + seq;
  return {
    tap: { seq, t0: 100 + s, t1: 175 + s, rtpTimestamp: rtp, receiveTime: 160 + s, miss: false },
    engine: [
      { kind: "input", key: seq, t1Us: e(104 + s), t2Us: e(105 + s), t3Us: 0 },
      { kind: "frame", key: rtp, t1Us: e(150 + s), t2Us: e(151 + s), t3Us: e(153 + s) },
    ],
  };
}

function input(count: number, overrides: Partial<AnalyzeInput> = {}): AnalyzeInput {
  const taps = Array.from({ length: count }, (_, i) => tap(i, i * 1000));
  return {
    taps: taps.map((t) => t.tap),
    frames: taps.map((t) => ({ rtpTimestamp: t.tap.rtpTimestamp!, receiveTime: t.tap.receiveTime!, expectedDisplayTime: t.tap.t1! })),
    engine: taps.flatMap((t) => t.engine),
    engineWrapped: false,
    offsetUs: OFFSET_US,
    driftUs: 100,
    transportChanged: false,
    ...overrides,
  };
}

test("percentile interpolates between ranks", () => {
  expect(percentile([1, 2, 3, 4], 0.5)).toBe(2.5);
  expect(percentile([1, 2, 3, 4], 1)).toBe(4);
});

test("bootstrap interval of identical values is zero", () => {
  expect(bootstrapP50CiHalf([5, 5, 5, 5], Math.random, 200)).toBe(0);
});

test("computes every stage from joined tap, engine and frame data", () => {
  const { stats, validity, missRate } = analyze(input(20), fixedRandom);
  expect(validity).toEqual({ failed: false, reasons: [], crossClockReliable: true, breakdownAvailable: true });
  expect(missRate).toBe(0);
  expect(stats.tap_to_photon!.p50).toBeCloseTo(75);
  expect(stats.input_network!.p50).toBeCloseTo(4);
  expect(stats.engine_input!.p50).toBeCloseTo(1);
  expect(stats.guest!.p50).toBeCloseTo(45);
  expect(stats.engine_video!.p50).toBeCloseTo(3);
  expect(stats.network_receive!.p50).toBeCloseTo(7);
  expect(stats.receiver!.p50).toBeCloseTo(15);
  expect(stats.tap_to_photon!.count).toBe(20);
});

test("more than 5% misses fails the run", () => {
  const data = input(20);
  data.taps[0] = { ...data.taps[0], miss: true, t1: null };
  data.taps[1] = { ...data.taps[1], miss: true, t1: null };
  const { validity, missRate } = analyze(data, fixedRandom);
  expect(missRate).toBeCloseTo(0.1);
  expect(validity.failed).toBe(true);
});

test("drift over 2 ms marks cross-clock stages unreliable but keeps the headline", () => {
  const { stats, validity } = analyze(input(20, { driftUs: 2_500 }), fixedRandom);
  expect(validity.crossClockReliable).toBe(false);
  expect(validity.failed).toBe(false);
  expect(stats.tap_to_photon).toBeDefined();
});

test("a wrapped engine log omits the breakdown", () => {
  const { stats, validity } = analyze(input(20, { engineWrapped: true }), fixedRandom);
  expect(validity.breakdownAvailable).toBe(false);
  expect(Object.keys(stats)).toEqual(["tap_to_photon"]);
});

test("a mid-run reconnect that breaks the RTP join omits the breakdown", () => {
  const data = input(20);
  data.engine = data.engine.map((entry) => (entry.kind === "frame" ? { ...entry, key: entry.key + 500_000 } : entry));
  const { stats, validity } = analyze(data, fixedRandom);
  expect(validity.breakdownAvailable).toBe(false);
  expect(validity.reasons.join(" ")).toContain("did not match");
  expect(Object.keys(stats)).toEqual(["tap_to_photon"]);
});

test("frames from a second viewer do not disturb the join", () => {
  const data = input(20);
  data.engine.push(...Array.from({ length: 50 }, (_, i) => ({ kind: "frame" as const, key: 3_000_000 + i, t1Us: 1, t2Us: 2, t3Us: 3 })));
  const { stats, validity } = analyze(data, fixedRandom);
  expect(validity.breakdownAvailable).toBe(true);
  expect(stats.guest!.count).toBe(20);
});

test("a transport change fails the run", () => {
  expect(analyze(input(20, { transportChanged: true }), fixedRandom).validity.failed).toBe(true);
});
```

`packages/core/src/bench/transport.test.ts`:

```ts
import { selectedCandidatePair } from "./transport";

test("reads the selected pair and its remote candidate type", () => {
  const stats = new Map<string, any>([
    ["T1", { type: "transport", selectedCandidatePairId: "P2" }],
    ["P1", { type: "candidate-pair", id: "P1", state: "succeeded", remoteCandidateId: "R1" }],
    ["P2", { type: "candidate-pair", id: "P2", state: "succeeded", remoteCandidateId: "R2" }],
    ["R1", { type: "remote-candidate", id: "R1", candidateType: "host" }],
    ["R2", { type: "remote-candidate", id: "R2", candidateType: "relay" }],
  ]);
  expect(selectedCandidatePair(stats)).toEqual({ id: "P2", remoteType: "relay" });
});

test("returns nulls without a selected pair", () => {
  expect(selectedCandidatePair(new Map())).toEqual({ id: null, remoteType: null });
});
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `npm run test:core -- bench`
Expected: FAIL — cannot find modules `./detect`, `./taps`, `./analyze`, `./transport`.

- [ ] **Step 4: Implement detect.ts**

```ts
export type TargetColor = "black" | "white";

export const WHITE_MIN = 160;
export const BLACK_MAX = 96;

// Mean Rec.601 luma of the central half of the image (each dimension), so
// letterboxing or a status overlay at the edges does not count.
export function centerLuminance(rgba: ArrayLike<number>, width: number, height: number): number {
  const x0 = Math.floor(width / 4), x1 = Math.ceil((width * 3) / 4);
  const y0 = Math.floor(height / 4), y1 = Math.ceil((height * 3) / 4);
  let sum = 0;
  let count = 0;
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      const i = (y * width + x) * 4;
      sum += 0.299 * rgba[i] + 0.587 * rgba[i + 1] + 0.114 * rgba[i + 2];
      count++;
    }
  }
  return count === 0 ? 0 : sum / count;
}

export function classify(lum: number): TargetColor | null {
  if (lum >= WHITE_MIN) return "white";
  if (lum <= BLACK_MAX) return "black";
  return null;
}

export function opposite(color: TargetColor): TargetColor {
  return color === "white" ? "black" : "white";
}
```

- [ ] **Step 5: Implement taps.ts**

```ts
import { classify, opposite } from "./detect";
import type { TargetColor } from "./detect";

export type FrameSample = {
  lum: number;
  rtpTimestamp: number | null;
  receiveTime: number | null;
  expectedDisplayTime: number;
};

export type TapRecord = {
  seq: number;
  t0: number;
  t1: number | null;
  rtpTimestamp: number | null;
  receiveTime: number | null;
  miss: boolean;
};

export type TapDeps = {
  sendClick(seq: number): void;
  // Resolves with the next presented frame, or null after timeoutMs.
  nextFrame(timeoutMs: number): Promise<FrameSample | null>;
  now(): number;
  sleep(ms: number): Promise<void>;
  random(): number;
};

export type TapOptions = {
  count: number;
  warmup: number;
  missTimeoutMs: number;
  gapMinMs: number;
  gapMaxMs: number;
  stableMs: number;
};

export const DEFAULT_TAP_OPTIONS: TapOptions = {
  count: 200, warmup: 10, missTimeoutMs: 2000, gapMinMs: 300, gapMaxMs: 700, stableMs: 1000,
};

export class TargetNotInFrontError extends Error {
  constructor() {
    super("target page not in front");
  }
}

async function readStableColor(deps: TapDeps, stableMs: number): Promise<TargetColor> {
  const start = deps.now();
  let color: TargetColor | null = null;
  while (deps.now() - start < stableMs) {
    const frame = await deps.nextFrame(stableMs);
    const current = frame ? classify(frame.lum) : null;
    if (current === null || (color !== null && current !== color)) throw new TargetNotInFrontError();
    color = current;
  }
  if (color === null) throw new TargetNotInFrontError();
  return color;
}

export async function runTaps(deps: TapDeps, options: TapOptions = DEFAULT_TAP_OPTIONS): Promise<TapRecord[]> {
  let current = await readStableColor(deps, options.stableMs);
  const records: TapRecord[] = [];

  for (let seq = 0; seq < options.warmup + options.count; seq++) {
    const expected = opposite(current);
    const t0 = deps.now();
    deps.sendClick(seq);

    let record: TapRecord = { seq, t0, t1: null, rtpTimestamp: null, receiveTime: null, miss: true };
    for (;;) {
      const remaining = options.missTimeoutMs - (deps.now() - t0);
      const frame = remaining > 0 ? await deps.nextFrame(remaining) : null;
      if (!frame) break;
      if (frame.expectedDisplayTime >= t0 && classify(frame.lum) === expected) {
        record = {
          seq, t0, t1: frame.expectedDisplayTime,
          rtpTimestamp: frame.rtpTimestamp, receiveTime: frame.receiveTime, miss: false,
        };
        break;
      }
    }

    if (record.miss) {
      // The click may still land late; re-read the screen before the next tap.
      const frame = await deps.nextFrame(options.missTimeoutMs);
      current = (frame && classify(frame.lum)) || current;
    } else {
      current = expected;
    }
    if (seq >= options.warmup) records.push(record);
    await deps.sleep(options.gapMinMs + deps.random() * (options.gapMaxMs - options.gapMinMs));
  }
  return records;
}
```

- [ ] **Step 6: Implement analyze.ts**

```ts
import { engineToLocalMs } from "./clock";
import type { EngineEntry } from "./benchClient";
import type { TapRecord } from "./taps";

export const STAGES = [
  "input_network", "engine_input", "guest", "engine_video", "network_receive", "receiver", "tap_to_photon",
] as const;
export type StageName = (typeof STAGES)[number];

export type StageStats = { count: number; mean: number; p50: number; p95: number; p99: number; p50CiHalf: number };
export type FrameMeta = { rtpTimestamp: number; receiveTime: number; expectedDisplayTime: number };
export type AnalyzeInput = {
  taps: TapRecord[];
  frames: FrameMeta[];
  engine: EngineEntry[];
  engineWrapped: boolean;
  offsetUs: number;
  driftUs: number;
  transportChanged: boolean;
};
export type Validity = { failed: boolean; reasons: string[]; crossClockReliable: boolean; breakdownAvailable: boolean };
export type Analysis = { stats: Partial<Record<StageName, StageStats>>; validity: Validity; missRate: number };

export const MAX_DRIFT_US = 2000;
export const MAX_MISS_RATE = 0.05;
const MIN_JOIN_RATE = 0.5;

export function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return NaN;
  const rank = (sorted.length - 1) * p;
  const lo = Math.floor(rank);
  const hi = Math.ceil(rank);
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (rank - lo);
}

export function bootstrapP50CiHalf(values: number[], random: () => number, iterations = 1000): number {
  if (values.length < 2) return 0;
  const medians: number[] = [];
  for (let i = 0; i < iterations; i++) {
    const resample = values.map(() => values[Math.floor(random() * values.length)]).sort((a, b) => a - b);
    medians.push(percentile(resample, 0.5));
  }
  medians.sort((a, b) => a - b);
  return (percentile(medians, 0.975) - percentile(medians, 0.025)) / 2;
}

function summarize(values: number[], random: () => number): StageStats {
  const sorted = [...values].sort((a, b) => a - b);
  return {
    count: sorted.length,
    mean: sorted.reduce((sum, v) => sum + v, 0) / sorted.length,
    p50: percentile(sorted, 0.5),
    p95: percentile(sorted, 0.95),
    p99: percentile(sorted, 0.99),
    p50CiHalf: bootstrapP50CiHalf(sorted, random),
  };
}

export function analyze(input: AnalyzeInput, random: () => number = Math.random): Analysis {
  const reasons: string[] = [];
  let failed = false;

  const measured = input.taps.filter((t) => !t.miss && t.t1 !== null);
  const missRate = input.taps.length === 0 ? 1 : (input.taps.length - measured.length) / input.taps.length;
  if (missRate > MAX_MISS_RATE) {
    failed = true;
    reasons.push(`miss rate ${(missRate * 100).toFixed(1)}% exceeds 5%`);
  }
  if (input.transportChanged) {
    failed = true;
    reasons.push("transport changed during the run");
  }
  const crossClockReliable = input.driftUs <= MAX_DRIFT_US;
  if (!crossClockReliable) {
    reasons.push(`clock drift ${(input.driftUs / 1000).toFixed(2)} ms exceeds 2 ms; cross-clock stages unreliable`);
  }

  const inputs = new Map<number, EngineEntry>();
  const frames = new Map<number, EngineEntry>();
  for (const entry of input.engine) (entry.kind === "input" ? inputs : frames).set(entry.key, entry);
  const local = (us: number) => engineToLocalMs(us, input.offsetUs);

  const values = Object.fromEntries(STAGES.map((s) => [s, [] as number[]])) as Record<StageName, number[]>;
  let joined = 0;
  for (const tap of measured) {
    values.tap_to_photon.push(tap.t1! - tap.t0);
    const inp = inputs.get(tap.seq);
    const frame = tap.rtpTimestamp === null ? undefined : frames.get(tap.rtpTimestamp);
    if (!inp || !frame || tap.receiveTime === null) continue;
    joined++;
    values.input_network.push(local(inp.t1Us) - tap.t0);
    values.engine_input.push((inp.t2Us - inp.t1Us) / 1000);
    values.guest.push((frame.t1Us - inp.t2Us) / 1000);
    values.receiver.push(tap.t1! - tap.receiveTime);
  }
  // Every received frame, not only tap frames, for the two per-frame stages.
  for (const meta of input.frames) {
    const frame = frames.get(meta.rtpTimestamp);
    if (!frame) continue;
    values.engine_video.push((frame.t3Us - frame.t1Us) / 1000);
    values.network_receive.push(meta.receiveTime - local(frame.t3Us));
  }

  let breakdownAvailable = true;
  if (input.engineWrapped || input.engine.length === 0) {
    breakdownAvailable = false;
    reasons.push("engine log missing or wrapped; breakdown omitted");
  } else if (measured.length > 0 && joined / measured.length < MIN_JOIN_RATE) {
    breakdownAvailable = false;
    reasons.push("engine entries did not match the measured taps (stream restarted?); breakdown omitted");
  }

  const stats: Partial<Record<StageName, StageStats>> = {};
  if (values.tap_to_photon.length > 0) stats.tap_to_photon = summarize(values.tap_to_photon, random);
  if (breakdownAvailable) {
    for (const stage of STAGES) {
      if (stage !== "tap_to_photon" && values[stage].length > 0) stats[stage] = summarize(values[stage], random);
    }
  }
  return { stats, validity: { failed, reasons, crossClockReliable, breakdownAvailable }, missRate };
}
```

- [ ] **Step 7: Implement transport.ts**

```ts
export type SelectedPair = { id: string | null; remoteType: string | null };

export function selectedCandidatePair(stats: { forEach(cb: (report: any) => void): void }): SelectedPair {
  const reports: any[] = [];
  stats.forEach((report) => reports.push(report));
  const byId = new Map(reports.map((r) => [r.id, r]));
  const selectedId = reports.find((r) => r.type === "transport" && r.selectedCandidatePairId)?.selectedCandidatePairId
    ?? reports.find((r) => r.type === "candidate-pair" && r.state === "succeeded" && r.nominated)?.id
    ?? null;
  const pair = selectedId ? byId.get(selectedId) : undefined;
  if (!pair) return { id: null, remoteType: null };
  return { id: pair.id, remoteType: byId.get(pair.remoteCandidateId)?.candidateType ?? null };
}
```

Note: the `transport.test.ts` map stores reports without an `id` for `T1`; `byId` keys it as `undefined`, which is harmless.

- [ ] **Step 8: Export the new modules**

`packages/core/src/bench/index.ts`:

```ts
export * from "./clock";
export * from "./benchClient";
export * from "./detect";
export * from "./taps";
export * from "./analyze";
export * from "./transport";
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `npm run test:core`
Expected: all PASS.

- [ ] **Step 10: Commit**

```bash
git add packages/core/src/bench
git commit -m "feat(core): add bench tap loop, flip detection and latency analysis"
```

---

### Task 8: `/bench` page in the web app

**Files:**
- Create: `apps/web/src/app/bench/page.tsx`
- Create: `apps/web/src/app/bench/BenchPageClient.tsx`
- Create: `apps/web/src/app/bench/runBench.ts`
- Create: `apps/web/src/app/bench/page.test.tsx`

**Interfaces:**
- Consumes: Task 5's `connectWhep({... onMessage})`; Tasks 6–7's `makeBenchClient`, `clockDriftUs`, `runTaps`, `DEFAULT_TAP_OPTIONS`, `centerLuminance`, `analyze`, `selectedCandidatePair`, types `BenchChannel`, `FrameSample`, `FrameMeta`, `TapRecord`, `EngineEntry`, `Analysis`; `useServer()` → `{ ready, paired, client }` where `client.select(serial): Promise<SelectResp>`.
- Produces (read by the runner in Task 9):
  - URL: `/bench?serial=<serial>&n=<count>&warmup=<count>`
  - `window.__benchResult: BenchResult` on success, `window.__benchError: string` on failure
  - `type BenchResult = { meta: { serial: string; tier: string | null; transport: string | null; offsetUs: number; driftUs: number; syncRttMs: number; count: number; warmup: number; userAgent: string; startedAt: string }; taps: TapRecord[]; frames: FrameMeta[]; engine: EngineEntry[]; stats: Analysis["stats"]; validity: Analysis["validity"]; missRate: number }`

- [ ] **Step 1: Write the failing page test**

`apps/web/src/app/bench/page.test.tsx`:

```tsx
import React from "react";
import { render } from "@testing-library/react";
import { ServerProvider } from "@wc/core";
import BenchPage from "./page";
import { plainStorage, secureStorage } from "../../platform/storage";

jest.mock("next/navigation", () => ({
  useRouter: () => ({ push: jest.fn(), replace: jest.fn() }),
  useSearchParams: () => new URLSearchParams("serial=emulator-5556&n=5"),
}));

test("BenchPage renders under the server provider without starting a run", () => {
  expect(() =>
    render(
      <ServerProvider plainStorage={plainStorage} secureStorage={secureStorage}>
        <BenchPage />
      </ServerProvider>
    )
  ).not.toThrow();
  expect((window as any).__benchResult).toBeUndefined();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -w apps/web -- bench`
Expected: FAIL — cannot find module `./page`.

- [ ] **Step 3: Write runBench.ts**

`apps/web/src/app/bench/runBench.ts`:

```ts
import {
  analyze, centerLuminance, clockDriftUs, connectWhep, DEFAULT_TAP_OPTIONS,
  makeBenchClient, runTaps, selectedCandidatePair,
} from "@wc/core";
import type {
  Analysis, BenchChannel, EngineEntry, FrameMeta, FrameSample, SelectResp, TapRecord,
} from "@wc/core";

export type BenchResult = {
  meta: {
    serial: string; tier: string | null; transport: string | null; offsetUs: number; driftUs: number;
    syncRttMs: number; count: number; warmup: number; userAgent: string; startedAt: string;
  };
  taps: TapRecord[];
  frames: FrameMeta[];
  engine: EngineEntry[];
  stats: Analysis["stats"];
  validity: Analysis["validity"];
  missRate: number;
};

type RunBenchOpts = {
  select: (serial: string) => Promise<SelectResp>;
  serial: string;
  count: number;
  warmup: number;
  video: HTMLVideoElement;
  onStatus: (status: string) => void;
};

const SAMPLE_SIZE = 8;

export async function runBench({ select, serial, count, warmup, video, onStatus }: RunBenchOpts): Promise<BenchResult> {
  const startedAt = new Date().toISOString();
  onStatus("selecting instance");
  const selection = await select(serial);

  const subscribers = new Set<(message: any) => void>();
  onStatus("connecting");
  const session = await connectWhep({
    whepUrl: selection.whep_url,
    whepToken: selection.whep_token || "",
    iceServers: selection.ice_servers || [],
    RTCImpl: window.RTCPeerConnection,
    onStream: (stream) => { video.srcObject = stream; },
    onInputRtt: () => {},
    onState: () => {},
    onMessage: (message) => subscribers.forEach((handler) => handler(message)),
  });
  await video.play().catch(() => {});

  const channel: BenchChannel = {
    send: (message) => session.input.send(message),
    subscribe: (handler) => { subscribers.add(handler); return () => { subscribers.delete(handler); }; },
  };

  // Frame pump: every presented frame is sampled once and handed to all waiters.
  const canvas = document.createElement("canvas");
  canvas.width = SAMPLE_SIZE;
  canvas.height = SAMPLE_SIZE;
  const context = canvas.getContext("2d", { willReadFrequently: true })!;
  const frames: FrameMeta[] = [];
  let waiters: Array<(sample: FrameSample) => void> = [];
  let stopped = false;
  const onFrame = (_now: number, meta: any) => {
    context.drawImage(video, 0, 0, SAMPLE_SIZE, SAMPLE_SIZE);
    const lum = centerLuminance(context.getImageData(0, 0, SAMPLE_SIZE, SAMPLE_SIZE).data, SAMPLE_SIZE, SAMPLE_SIZE);
    const sample: FrameSample = {
      lum,
      rtpTimestamp: typeof meta.rtpTimestamp === "number" ? meta.rtpTimestamp : null,
      receiveTime: typeof meta.receiveTime === "number" ? meta.receiveTime : null,
      expectedDisplayTime: meta.expectedDisplayTime,
    };
    if (sample.rtpTimestamp !== null && sample.receiveTime !== null) {
      frames.push({ rtpTimestamp: sample.rtpTimestamp, receiveTime: sample.receiveTime, expectedDisplayTime: sample.expectedDisplayTime });
    }
    const ready = waiters;
    waiters = [];
    ready.forEach((resolve) => resolve(sample));
    if (!stopped) (video as any).requestVideoFrameCallback(onFrame);
  };
  (video as any).requestVideoFrameCallback(onFrame);

  const nextFrame = (timeoutMs: number) =>
    new Promise<FrameSample | null>((resolve) => {
      const done = (sample: FrameSample) => { clearTimeout(timer); resolve(sample); };
      const timer = setTimeout(() => { waiters = waiters.filter((w) => w !== done); resolve(null); }, timeoutMs);
      waiters.push(done);
    });

  try {
    const bench = makeBenchClient(channel, { now: () => performance.now() });
    onStatus("syncing clocks");
    const before = await bench.syncClock();
    await bench.start();
    const pairBefore = selectedCandidatePair(await session.pc.getStats());

    onStatus(`tapping (${warmup} warm-up + ${count})`);
    const taps = await runTaps({
      sendClick: (seq) => session.input.send({ type: "click", x: 0.5, y: 0.5, bench_seq: seq }),
      nextFrame,
      now: () => performance.now(),
      sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
      random: Math.random,
    }, { ...DEFAULT_TAP_OPTIONS, count, warmup });

    const pairAfter = selectedCandidatePair(await session.pc.getStats());
    onStatus("syncing clocks");
    const after = await bench.syncClock();
    onStatus("fetching engine log");
    const engineLog = await bench.fetchAll();
    stopped = true;

    const driftUs = clockDriftUs(before, after);
    const analysis = analyze({
      taps, frames, engine: engineLog.entries, engineWrapped: engineLog.wrapped,
      offsetUs: before.offsetUs, driftUs, transportChanged: pairBefore.id !== pairAfter.id,
    });
    return {
      meta: {
        serial, tier: selection.tier ?? null, transport: pairAfter.remoteType, offsetUs: before.offsetUs,
        driftUs, syncRttMs: before.rttMs, count, warmup, userAgent: navigator.userAgent, startedAt,
      },
      taps, frames, engine: engineLog.entries,
      stats: analysis.stats, validity: analysis.validity, missRate: analysis.missRate,
    };
  } finally {
    stopped = true;
    await session.close();
  }
}
```

If `SelectResp` is not re-exported from `@wc/core`, it is: `packages/core/src/index.ts` already has `export * from "./api/client";`.

- [ ] **Step 4: Write the page and client component**

`apps/web/src/app/bench/page.tsx`:

```tsx
import { Suspense } from "react";
import BenchPageClient from "./BenchPageClient";

// Lab-only latency bench (scripts/bench). The host serves this page only
// when started with EMUCTRL_BENCH=1.
export default function BenchPage() {
  return (
    <Suspense>
      <BenchPageClient />
    </Suspense>
  );
}
```

`apps/web/src/app/bench/BenchPageClient.tsx`:

```tsx
"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useServer } from "@wc/core";
import { runBench } from "./runBench";

const intParam = (params: URLSearchParams, name: string, fallback: number) => {
  const value = Number.parseInt(params.get(name) ?? "", 10);
  return Number.isFinite(value) && value > 0 ? value : fallback;
};

export default function BenchPageClient() {
  const router = useRouter();
  const params = useSearchParams();
  const { ready, paired, client } = useServer();
  const videoRef = useRef<HTMLVideoElement>(null);
  const started = useRef(false);
  const [status, setStatus] = useState("waiting for server");

  useEffect(() => {
    if (!ready || paired === null) return;
    if (!paired) { router.replace("/pair"); return; }
    if (started.current || !videoRef.current || !client) return;
    started.current = true;

    runBench({
      select: (serial) => client.select(serial),
      serial: params.get("serial") ?? "",
      count: intParam(params, "n", 200),
      warmup: intParam(params, "warmup", 10),
      video: videoRef.current,
      onStatus: setStatus,
    }).then((result) => {
      (window as any).__benchResult = result;
      setStatus(result.validity.failed ? `done (failed: ${result.validity.reasons.join("; ")})` : "done");
    }).catch((error) => {
      const message = error instanceof Error ? error.message : String(error);
      (window as any).__benchError = message;
      setStatus(`error: ${message}`);
    });
  }, [ready, paired, client, params, router]);

  return (
    <div style={{ position: "fixed", inset: 0, background: "#222" }}>
      <video ref={videoRef} autoPlay playsInline muted
        style={{ width: "100%", height: "100%", objectFit: "contain" }} />
      {/* Kept in a corner: the flip detector samples only the centre. */}
      <div style={{ position: "absolute", left: 8, bottom: 8, color: "#ccc", font: "12px monospace" }}>
        bench: {status}
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Run tests and the static build**

Run: `npm test -w apps/web`
Expected: all PASS.

Run: `npm run build -w apps/web && ls apps/web/out/bench.html apps/web/out/bench.txt`
Expected: build succeeds; both files listed.

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/app/bench
git commit -m "feat(web): add the hidden latency bench page"
```

---

### Task 9: Playwright runner, report and compare

**Files:**
- Create: `scripts/bench/report.mjs`
- Create: `scripts/bench/report.test.mjs`
- Create: `scripts/bench/run.mjs`
- Create: `scripts/bench/README.md`
- Modify: `package.json` (root: scripts and devDependency)
- Modify: `.gitignore` (add `bench-results/`)

**Interfaces:**
- Consumes: Task 8's URL, `window.__benchResult` / `window.__benchError`, `BenchResult` shape (`stats[stage] = { count, mean, p50, p95, p99, p50CiHalf }`, `validity`, `meta`).
- Produces:
  - `report.mjs`: `STAGE_ORDER: string[]`; `formatSummary(result): string` (Markdown); `compareResults(a, b): Array<{ stage, p50A, p50B, deltaP50, p95A, p95B, deltaP95, significant }>`; `formatComparison(rows): string`; `resultBaseName(date: Date, label: string): string` → `YYYY-MM-DDTHHMM-<label>`
  - CLI: `npm run bench -- --host http://<ip>:8080 --serial <serial> [--n 200] [--warmup 10] [--label run]`, `npm run bench -- --setup --host ...`, `npm run bench -- --compare a.json b.json`. Exit 0 = valid run, 2 = run marked failed, 1 = error.

- [ ] **Step 1: Write the failing report tests**

`scripts/bench/report.test.mjs`:

```js
import { test } from "node:test";
import assert from "node:assert/strict";
import { compareResults, formatComparison, formatSummary, resultBaseName } from "./report.mjs";

const stage = (p50, p95, ci) => ({ count: 100, mean: p50, p50, p95, p99: p95 + 5, p50CiHalf: ci });
const result = (p50) => ({
  meta: { serial: "emulator-5556", tier: "720", transport: "host", driftUs: 300, count: 100, warmup: 10, commit: "abc1234", label: "x" },
  stats: { tap_to_photon: stage(p50, p50 + 20, 2), guest: stage(40, 60, 1) },
  validity: { failed: false, reasons: [], crossClockReliable: true, breakdownAvailable: true },
  missRate: 0,
});

test("names result files by local minute and label", () => {
  assert.equal(resultBaseName(new Date(2026, 9, 16, 9, 5), "baseline"), "2026-10-16T0905-baseline");
});

test("summary lists stages in pipeline order with p50/p95/p99", () => {
  const md = formatSummary(result(90));
  assert.match(md, /\| guest \| 100 \| 40\.0 \| 60\.0 \| 65\.0 \|/);
  assert.ok(md.indexOf("guest") < md.indexOf("tap_to_photon"));
  assert.match(md, /transport: host/);
});

test("compare flags only changes larger than the noise", () => {
  const rows = compareResults(result(90), result(80));
  const headline = rows.find((r) => r.stage === "tap_to_photon");
  assert.equal(headline.deltaP50, -10);
  assert.equal(headline.significant, true);
  assert.equal(rows.find((r) => r.stage === "guest").significant, false);
  assert.match(formatComparison(rows), /tap_to_photon \| 90\.0 \| 80\.0 \| -10\.0 \| .* \| yes/);
});

test("compare skips stages missing from either run", () => {
  const a = result(90);
  delete a.stats.guest;
  assert.deepEqual(compareResults(a, result(80)).map((r) => r.stage), ["tap_to_photon"]);
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `node --test scripts/bench/`
Expected: FAIL — cannot find module `./report.mjs`.

- [ ] **Step 3: Implement report.mjs**

`scripts/bench/report.mjs`:

```js
// Formatting and comparison for latency bench results (see run.mjs).
export const STAGE_ORDER = [
  "input_network", "engine_input", "guest", "engine_video", "network_receive", "receiver", "tap_to_photon",
];

const ms = (value) => (typeof value === "number" && Number.isFinite(value) ? value.toFixed(1) : "–");
const pad = (n) => String(n).padStart(2, "0");

export function resultBaseName(date, label) {
  const stamp = `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}${pad(date.getMinutes())}`;
  return `${stamp}-${label}`;
}

export function formatSummary(result) {
  const { meta, stats, validity, missRate } = result;
  const lines = [
    `# Latency bench — ${meta.label}`,
    "",
    `commit: ${meta.commit} · serial: ${meta.serial} · tier: ${meta.tier} · transport: ${meta.transport} · ` +
      `taps: ${meta.count} (+${meta.warmup} warm-up) · misses: ${(missRate * 100).toFixed(1)}% · ` +
      `clock drift: ${(meta.driftUs / 1000).toFixed(2)} ms`,
    "",
    `status: ${validity.failed ? "FAILED" : "ok"}${validity.reasons.length ? ` — ${validity.reasons.join("; ")}` : ""}`,
    "",
    "| stage | n | p50 ms | p95 ms | p99 ms | mean ms | p50 ± |",
    "|---|---|---|---|---|---|---|",
  ];
  for (const stage of STAGE_ORDER) {
    const s = stats[stage];
    if (!s) continue;
    lines.push(`| ${stage} | ${s.count} | ${ms(s.p50)} | ${ms(s.p95)} | ${ms(s.p99)} | ${ms(s.mean)} | ${ms(s.p50CiHalf)} |`);
  }
  return lines.join("\n") + "\n";
}

export function compareResults(a, b) {
  const rows = [];
  for (const stage of STAGE_ORDER) {
    const sa = a.stats[stage];
    const sb = b.stats[stage];
    if (!sa || !sb) continue;
    const deltaP50 = sb.p50 - sa.p50;
    rows.push({
      stage,
      p50A: sa.p50, p50B: sb.p50, deltaP50,
      p95A: sa.p95, p95B: sb.p95, deltaP95: sb.p95 - sa.p95,
      significant: Math.abs(deltaP50) > Math.max(sa.p50CiHalf, sb.p50CiHalf),
    });
  }
  return rows;
}

export function formatComparison(rows) {
  const lines = [
    "| stage | p50 A | p50 B | Δp50 | Δp95 | beyond noise |",
    "|---|---|---|---|---|---|",
  ];
  for (const r of rows) {
    lines.push(`| ${r.stage} | ${ms(r.p50A)} | ${ms(r.p50B)} | ${ms(r.deltaP50)} | ${ms(r.deltaP95)} | ${r.significant ? "yes" : "no"} |`);
  }
  return lines.join("\n") + "\n";
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test scripts/bench/`
Expected: 4 tests PASS.

- [ ] **Step 5: Add Playwright and npm scripts**

Root `package.json`: add to `"scripts"`:

```json
    "bench": "node scripts/bench/run.mjs",
    "test:bench": "node --test scripts/bench/"
```

and to `"devDependencies"`: `"playwright": "1.49.1"`. Then run `npm install` (updates the tracked root `package-lock.json`). No browser download is needed: the runner uses the installed Google Chrome (`channel: "chrome"`).

`.gitignore`: append

```
# Latency bench output (scripts/bench)
bench-results/
```

- [ ] **Step 6: Write the runner**

`scripts/bench/run.mjs`:

```js
#!/usr/bin/env node
// Lab latency bench runner. See scripts/bench/README.md.
import { chromium } from "playwright";
import { execFileSync } from "node:child_process";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import os from "node:os";
import path from "node:path";
import { parseArgs } from "node:util";
import { compareResults, formatComparison, formatSummary, resultBaseName } from "./report.mjs";

const { values, positionals } = parseArgs({
  allowPositionals: true,
  options: {
    host: { type: "string" },
    serial: { type: "string" },
    n: { type: "string", default: "200" },
    warmup: { type: "string", default: "10" },
    label: { type: "string", default: "run" },
    setup: { type: "boolean", default: false },
    compare: { type: "boolean", default: false },
    profile: { type: "string", default: path.join(os.homedir(), ".emuctrl-bench-profile") },
    out: { type: "string", default: "bench-results" },
  },
});

function fail(message) {
  console.error(`bench: ${message}`);
  process.exit(1);
}

async function launch() {
  return chromium.launchPersistentContext(values.profile, {
    channel: "chrome",
    headless: false, // a hidden tab throttles requestVideoFrameCallback
    viewport: { width: 1280, height: 800 },
  });
}

async function setup() {
  if (!values.host) fail("--host is required");
  const context = await launch();
  const page = context.pages()[0] ?? (await context.newPage());
  await page.goto(`${values.host}/pair`);
  console.log("Pair this browser in the opened window, then close it.");
  await context.waitForEvent("close", { timeout: 0 });
}

async function run() {
  if (!values.host || !values.serial) fail("--host and --serial are required");
  const count = Number(values.n);
  const warmup = Number(values.warmup);
  const context = await launch();
  try {
    const page = context.pages()[0] ?? (await context.newPage());
    const url = `${values.host}/bench?serial=${encodeURIComponent(values.serial)}&n=${count}&warmup=${warmup}`;
    await page.goto(url);
    // About 0.5 s per tap plus a 0.7 s worst-case gap, plus setup and fetch.
    const timeout = ((count + warmup) * 2.7 + 120) * 1000;
    await page.waitForFunction(() => window.__benchResult || window.__benchError, null, { timeout, polling: 500 });
    const error = await page.evaluate(() => window.__benchError);
    if (error) fail(error);
    const result = await page.evaluate(() => window.__benchResult);

    result.meta.label = values.label;
    result.meta.commit = execFileSync("git", ["rev-parse", "--short", "HEAD"]).toString().trim();
    mkdirSync(values.out, { recursive: true });
    const base = path.join(values.out, resultBaseName(new Date(), values.label));
    const summary = formatSummary(result);
    writeFileSync(`${base}.json`, JSON.stringify(result, null, 2));
    writeFileSync(`${base}.md`, summary);
    console.log(summary);
    console.log(`wrote ${base}.json and ${base}.md`);
    process.exitCode = result.validity.failed ? 2 : 0;
  } finally {
    await context.close();
  }
}

function compare() {
  if (positionals.length !== 2) fail("--compare needs two result files");
  const [a, b] = positionals.map((file) => JSON.parse(readFileSync(file, "utf8")));
  console.log(`A: ${positionals[0]}\nB: ${positionals[1]}\n`);
  console.log(formatComparison(compareResults(a, b)));
}

if (values.compare) compare();
else if (values.setup) await setup();
else await run();
```

- [ ] **Step 7: Write the operator README**

`scripts/bench/README.md`:

````markdown
# Latency bench

Measures tap-to-photon latency and a per-stage breakdown. Design:
`docs/superpowers/specs/2026-10-16-latency-bench-harness-design.md`.

## One-time setup

1. Windows host: create a separate LDPlayer instance for benching (keep the
   game in its own instance).
2. Start the host from source in bench mode:
   `set EMUCTRL_BENCH=1` then `uv run python src/main.py`. The engine inherits
   the variable. Never set it on a normal install.
3. In the bench instance's browser open
   `http://10.0.2.2:8080/bench/target.html` and tap once (it goes full-screen).
4. Mac: `npm install`, then `npm run bench -- --setup --host http://<host-ip>:8080`
   and pair the opened Chrome window with a code from the host.

## Run

```bash
npm run bench -- --host http://<host-ip>:8080 --serial <bench-serial> --label baseline
npm run bench -- --host http://<host-ip>:8080 --serial <bench-serial> --label intra-refresh
npm run bench -- --compare bench-results/<a>.json bench-results/<b>.json
```

Exit codes: 0 valid run, 2 run marked failed (see the reasons in the `.md`), 1 error.
Keep the Chrome window visible during a run.
````

- [ ] **Step 8: Run all JS tests**

Run: `npm run test:bench && npm run test:core && npm test -w apps/web`
Expected: all PASS.

- [ ] **Step 9: Commit**

```bash
git add scripts/bench package.json package-lock.json .gitignore
git commit -m "feat(bench): add the Playwright latency bench runner and compare"
```

---

### Task 10: End-to-end verification on real hardware

**Files:**
- Modify: `docs/superpowers/plans/2026-10-16-latency-bench-harness.md` (record results below)

**Interfaces:**
- Consumes: everything above.
- Produces: two recorded runs that show the harness works and how noisy it is.

- [ ] **Step 1: Confirm bench mode is off by default**

On Windows, start the host normally (no `EMUCTRL_BENCH`). From the Mac: `curl -s -o /dev/null -w "%{http_code}\n" http://<host-ip>:8080/bench` → Expected `404`.

- [ ] **Step 2: Start in bench mode and open the target**

Follow `scripts/bench/README.md` setup steps 2–4. Check that the engine log shows `[engine] bench mode on`.

- [ ] **Step 3: Run twice with no code change in between**

```bash
npm run bench -- --host http://<host-ip>:8080 --serial <bench-serial> --label noise-a
npm run bench -- --host http://<host-ip>:8080 --serial <bench-serial> --label noise-b
npm run bench -- --compare bench-results/<noise-a>.json bench-results/<noise-b>.json
```

Expected: both exit 0; every stage present; tap_to_photon p50 differs by under 5 ms; `beyond noise` is `no` for tap_to_photon. If drift > 2 ms or misses > 5%, record that and investigate before using the harness for comparisons.

- [ ] **Step 4: Check the breakdown adds up**

In one `.md`, the sum of the six stage p50s should be within about 10 ms of the tap_to_photon p50 (medians do not add exactly). A larger gap means a join or clock bug — investigate before continuing.

- [ ] **Step 5: Record results and commit**

Append a `## Verification results` section to this plan with both summary tables and the compare table, then:

```bash
git add -f docs/superpowers/plans/2026-10-16-latency-bench-harness.md
git commit -m "docs(bench): record the first latency bench runs"
```
