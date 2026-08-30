#include <winsock2.h>
#include <ws2tcpip.h>

#include <gtest/gtest.h>
#include "scrcpy_source.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <stdexcept>
#include <thread>
#include <vector>

#pragma comment(lib, "ws2_32.lib")

namespace {

class FakeScrcpyServer {
public:
    enum class HandshakeBehavior { Complete, CloseBeforeMetadata };

    explicit FakeScrcpyServer(
        int width = 100,
        int height = 200,
        HandshakeBehavior handshakeBehavior = HandshakeBehavior::Complete)
        : width_(width), height_(height), handshakeBehavior_(handshakeBehavior) {
        WSADATA wsaData;
        if (WSAStartup(MAKEWORD(2, 2), &wsaData) != 0) {
            throw std::runtime_error("WSAStartup failed");
        }
        wsaInitialized_ = true;
        listenSock_ = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (listenSock_ == INVALID_SOCKET) throw std::runtime_error("socket failed");

        sockaddr_in addr{};
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        addr.sin_port = 0;
        if (bind(listenSock_, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0 ||
            listen(listenSock_, 2) != 0) {
            throw std::runtime_error("bind/listen failed");
        }
        int addrLen = sizeof(addr);
        getsockname(listenSock_, reinterpret_cast<sockaddr*>(&addr), &addrLen);
        port_ = ntohs(addr.sin_port);
    }

    ~FakeScrcpyServer() {
        Stop();
        if (wsaInitialized_) WSACleanup();
    }

    FakeScrcpyServer(const FakeScrcpyServer&) = delete;
    FakeScrcpyServer& operator=(const FakeScrcpyServer&) = delete;

    int Port() const { return port_; }

    void Serve() {
        thread_ = std::thread([this]() { Run(); });
    }

    void Stop() {
        running_.store(false);
        {
            std::lock_guard<std::mutex> lock(socketMutex_);
            CloseSocket(videoSock_, false);
            CloseSocket(controlSock_, false);
            CloseSocket(listenSock_, false);
        }
        stoppedCv_.notify_all();
        if (thread_.joinable()) thread_.join();
    }

    void AbortVideo() {
        std::lock_guard<std::mutex> lock(socketMutex_);
        CloseSocket(videoSock_, true);
    }

    void AbortControl() {
        std::lock_guard<std::mutex> lock(socketMutex_);
        CloseSocket(controlSock_, true);
    }

private:
    static void CloseSocket(SOCKET& sock, bool resetAbort) {
        if (sock == INVALID_SOCKET) return;
        if (resetAbort) {
            linger option{1, 0};
            setsockopt(sock, SOL_SOCKET, SO_LINGER,
                       reinterpret_cast<const char*>(&option), sizeof(option));
        } else {
            shutdown(sock, SD_BOTH);
        }
        closesocket(sock);
        sock = INVALID_SOCKET;
    }

    bool AcceptInto(SOCKET& destination) {
        SOCKET listener;
        {
            std::lock_guard<std::mutex> lock(socketMutex_);
            listener = listenSock_;
        }
        if (listener == INVALID_SOCKET) return false;

        SOCKET accepted = accept(listener, nullptr, nullptr);
        std::lock_guard<std::mutex> lock(socketMutex_);
        if (accepted == INVALID_SOCKET || !running_.load()) {
            if (accepted != INVALID_SOCKET) closesocket(accepted);
            return false;
        }
        destination = accepted;
        return true;
    }

    bool SendAll(SOCKET& sock, const std::uint8_t* data, size_t size) {
        std::lock_guard<std::mutex> lock(socketMutex_);
        if (sock == INVALID_SOCKET) return false;
        size_t sent = 0;
        while (sent < size) {
            int result = send(sock, reinterpret_cast<const char*>(data + sent),
                              static_cast<int>(size - sent), 0);
            if (result <= 0) return false;
            sent += static_cast<size_t>(result);
        }
        return true;
    }

    void Run() {
        if (!AcceptInto(videoSock_)) return;
        const std::uint8_t dummy = 0;
        if (!SendAll(videoSock_, &dummy, 1)) return;

        // The control accept precedes video metadata exactly as it does in
        // scrcpy-server, so reversing the client's connect order deadlocks.
        if (!AcceptInto(controlSock_)) return;

        if (handshakeBehavior_ == HandshakeBehavior::CloseBeforeMetadata) {
            std::lock_guard<std::mutex> lock(socketMutex_);
            CloseSocket(videoSock_, true);
            CloseSocket(controlSock_, true);
            return;
        }

        std::vector<std::uint8_t> name(64, 0);
        constexpr char deviceName[] = "fake-device";
        std::copy(deviceName, deviceName + std::strlen(deviceName), name.begin());
        if (!SendAll(videoSock_, name.data(), name.size())) return;

        std::uint8_t meta[12] = {};
        WriteU32BE(meta + 4, static_cast<std::uint32_t>(width_));
        WriteU32BE(meta + 8, static_cast<std::uint32_t>(height_));
        if (!SendAll(videoSock_, meta, sizeof(meta))) return;

        std::unique_lock<std::mutex> lock(stopMutex_);
        stoppedCv_.wait(lock, [this]() { return !running_.load(); });
    }

    static void WriteU32BE(std::uint8_t* destination, std::uint32_t value) {
        destination[0] = static_cast<std::uint8_t>(value >> 24);
        destination[1] = static_cast<std::uint8_t>(value >> 16);
        destination[2] = static_cast<std::uint8_t>(value >> 8);
        destination[3] = static_cast<std::uint8_t>(value);
    }

    int width_;
    int height_;
    HandshakeBehavior handshakeBehavior_;
    SOCKET listenSock_ = INVALID_SOCKET;
    SOCKET videoSock_ = INVALID_SOCKET;
    SOCKET controlSock_ = INVALID_SOCKET;
    int port_ = 0;
    std::thread thread_;
    std::atomic<bool> running_{true};
    std::mutex socketMutex_;
    std::mutex stopMutex_;
    std::condition_variable stoppedCv_;
    bool wsaInitialized_ = false;
};

template <typename Predicate>
bool PollUntil(Predicate predicate) {
    for (int attempt = 0; attempt < 100; ++attempt) {
        if (predicate()) return true;
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    return predicate();
}

} // namespace

TEST(ScrcpySource, ConnectInitialSucceedsAndReportsDimensions) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    {
        ScrcpySource source(registry);
        source.ConnectInitial(fake.Port());

        auto status = source.Status();
        EXPECT_EQ(status.width, 100);
        EXPECT_EQ(status.height, 200);
        EXPECT_EQ(status.generation, 0u);
        EXPECT_EQ(status.state, SourceHealthState::Connected);
        EXPECT_NE(source.Control(), nullptr);
    }
}

TEST(ScrcpySource, RejectsStaleAndEqualReconnectGenerationsWithoutStateChange) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    {
        ScrcpySource source(registry);
        source.ConnectInitial(fake.Port());
        auto control = source.Control();
        auto before = source.Status();

        EXPECT_FALSE(source.Reconnect(fake.Port(), 0));
        EXPECT_FALSE(source.Reconnect(fake.Port(), before.generation));

        auto after = source.Status();
        EXPECT_EQ(after.generation, before.generation);
        EXPECT_EQ(after.width, before.width);
        EXPECT_EQ(after.height, before.height);
        EXPECT_EQ(source.Control(), control);
    }
}

TEST(ScrcpySource, ReconnectAdvancesGenerationAndPreservesPeerRegistry) {
    FakeScrcpyServer first(100, 200);
    FakeScrcpyServer second(300, 400);
    first.Serve();
    second.Serve();
    PeerRegistry registry;
    auto peer = registry.Create(PeerKind::Local, "existing-peer", {});
    ASSERT_NE(peer, nullptr);
    {
        ScrcpySource source(registry);
        source.ConnectInitial(first.Port());
        ASSERT_TRUE(source.Reconnect(second.Port(), 2));

        auto status = source.Status();
        EXPECT_EQ(status.generation, 2u);
        EXPECT_EQ(status.width, 300);
        EXPECT_EQ(status.height, 400);
        EXPECT_EQ(status.state, SourceHealthState::Connected);
        EXPECT_EQ(registry.Find("existing-peer"), peer);
    }
}

TEST(ScrcpySource, FailedReconnectKeepsCommittedMetadataAndRetiresControl) {
    FakeScrcpyServer first(100, 200);
    FakeScrcpyServer broken(
        300, 400, FakeScrcpyServer::HandshakeBehavior::CloseBeforeMetadata);
    first.Serve();
    broken.Serve();
    PeerRegistry registry;
    {
        ScrcpySource source(registry);
        source.ConnectInitial(first.Port());

        EXPECT_THROW(source.Reconnect(broken.Port(), 3), std::runtime_error);

        auto status = source.Status();
        EXPECT_EQ(status.generation, 0u);
        EXPECT_EQ(status.width, 100);
        EXPECT_EQ(status.height, 200);
        EXPECT_EQ(status.state, SourceHealthState::Disconnected);
        EXPECT_EQ(source.Control(), nullptr);
    }
}

TEST(ScrcpySource, FailedInitialHandshakeLeavesDisconnectedWithoutControl) {
    FakeScrcpyServer broken(
        100, 200, FakeScrcpyServer::HandshakeBehavior::CloseBeforeMetadata);
    broken.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);

    EXPECT_THROW(source.ConnectInitial(broken.Port()), std::runtime_error);

    auto status = source.Status();
    EXPECT_EQ(status.state, SourceHealthState::Disconnected);
    EXPECT_EQ(status.generation, 0u);
    EXPECT_EQ(status.width, 0);
    EXPECT_EQ(status.height, 0);
    EXPECT_EQ(source.Control(), nullptr);
}

TEST(ScrcpySource, ReportsStalledAfterInactivityThreshold) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    {
        ScrcpySource source(registry, std::chrono::milliseconds(50));
        source.ConnectInitial(fake.Port());

        EXPECT_TRUE(PollUntil([&]() {
            return source.Status().state == SourceHealthState::Stalled;
        }));
    }
}

TEST(ScrcpySource, UnexpectedVideoEofReportsDisconnectedImmediately) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    {
        ScrcpySource source(registry, std::chrono::seconds(30));
        source.ConnectInitial(fake.Port());
        fake.AbortVideo();

        EXPECT_TRUE(PollUntil([&]() {
            return source.Status().state == SourceHealthState::Disconnected;
        }));
    }
}

TEST(ScrcpySource, ControlSendFailureReportsDisconnectedImmediately) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    {
        ScrcpySource source(registry, std::chrono::seconds(30));
        source.ConnectInitial(fake.Port());
        fake.AbortControl();

        EXPECT_TRUE(PollUntil([&]() {
            source.RequestIdr();
            return source.Status().state == SourceHealthState::Disconnected;
        }));
    }
}
