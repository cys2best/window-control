#pragma once

#include <atomic>
#include <chrono>
#include <string>
#include <thread>

#if defined(_WIN32)
#include <process.h>
#else
#include <unistd.h>
#endif

namespace signaling_test {

inline long ProcessId() {
#if defined(_WIN32)
    return static_cast<long>(_getpid());
#else
    return static_cast<long>(getpid());
#endif
}

inline std::string UniqueSession(const std::string& stem) {
    static const auto processNonce =
        std::chrono::steady_clock::now().time_since_epoch().count();
    static std::atomic<unsigned long long> sequence{0};
    return "engine-test-" + stem + "-" + std::to_string(ProcessId()) + "-" +
           std::to_string(processNonce) + "-" +
           std::to_string(sequence.fetch_add(1));
}

template <typename Predicate>
bool WaitUntil(Predicate predicate, std::chrono::milliseconds timeout) {
    const auto deadline = std::chrono::steady_clock::now() + timeout;
    while (std::chrono::steady_clock::now() < deadline) {
        if (predicate()) return true;
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    return predicate();
}

}  // namespace signaling_test
