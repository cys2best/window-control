#pragma once

#include <winsock2.h>
#include <ws2tcpip.h>

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

    bool SendAccessUnit(const std::vector<std::uint8_t>& accessUnit) {
        std::vector<std::uint8_t> frame(12, 0);
        WriteU32BE(frame.data() + 8, static_cast<std::uint32_t>(accessUnit.size()));
        frame.insert(frame.end(), accessUnit.begin(), accessUnit.end());
        return SendAll(videoSock_, frame.data(), frame.size());
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
