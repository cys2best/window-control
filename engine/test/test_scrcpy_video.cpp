// engine/test/test_scrcpy_video.cpp
#include <gtest/gtest.h>
#include "scrcpy_video.h"
#include <winsock2.h>
#include <ws2tcpip.h>
#include <thread>
#include <vector>
#include <cstring>
#include <atomic>
#include <chrono>
#include <mutex>
#include <stdexcept>

#pragma comment(lib, "ws2_32.lib")

namespace {

// Starts a listening socket on an ephemeral port, accepts exactly one
// connection, writes `script` to it, then closes. Returns the bound port.
int StartFakeScrcpyServer(const std::vector<uint8_t>& script) {
    WSADATA wsaData;
    WSAStartup(MAKEWORD(2, 2), &wsaData);

    SOCKET listenSock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = 0; // ephemeral
    bind(listenSock, reinterpret_cast<sockaddr*>(&addr), sizeof(addr));
    listen(listenSock, 1);

    int addrLen = sizeof(addr);
    getsockname(listenSock, reinterpret_cast<sockaddr*>(&addr), &addrLen);
    int port = ntohs(addr.sin_port);

    std::thread([listenSock, script]() {
        SOCKET client = accept(listenSock, nullptr, nullptr);
        send(client, reinterpret_cast<const char*>(script.data()), static_cast<int>(script.size()), 0);
        // Keep the socket open briefly so the client can finish reading
        // before this thread (and the fake server) tears down.
        std::this_thread::sleep_for(std::chrono::milliseconds(200));
        closesocket(client);
        closesocket(listenSock);
    }).detach();

    return port;
}

std::vector<uint8_t> BuildHandshakeAndOneFrame() {
    std::vector<uint8_t> script;
    script.push_back(0x00); // dummy byte

    // 64-byte device name, zero-padded
    std::string deviceName = "test-device";
    std::vector<uint8_t> nameBuf(64, 0);
    std::memcpy(nameBuf.data(), deviceName.data(), deviceName.size());
    script.insert(script.end(), nameBuf.begin(), nameBuf.end());

    // 12-byte meta: codec_id (4) + width (4) + height (4), big-endian
    auto pushU32BE = [&script](uint32_t v) {
        script.push_back(static_cast<uint8_t>((v >> 24) & 0xFF));
        script.push_back(static_cast<uint8_t>((v >> 16) & 0xFF));
        script.push_back(static_cast<uint8_t>((v >> 8) & 0xFF));
        script.push_back(static_cast<uint8_t>(v & 0xFF));
    };
    pushU32BE(0x68323634); // arbitrary codec_id ("h264"-ish, value unchecked by client)
    pushU32BE(1280);       // width
    pushU32BE(720);        // height

    // One frame: 12-byte header (8-byte pts_flags + 4-byte size, big-endian) + payload
    auto pushU64BE = [&script](uint64_t v) {
        for (int i = 7; i >= 0; --i) script.push_back(static_cast<uint8_t>((v >> (i * 8)) & 0xFF));
    };
    std::vector<uint8_t> payload = {0x00, 0x00, 0x00, 0x01, 0x65, 0xAA, 0xBB}; // fake Annex-B-ish bytes
    pushU64BE(0); // pts_flags
    pushU32BE(static_cast<uint32_t>(payload.size()));
    script.insert(script.end(), payload.begin(), payload.end());

    return script;
}

class IdleVideoServer {
public:
    IdleVideoServer() {
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
            listen(listenSock_, 1) != 0) {
            throw std::runtime_error("bind/listen failed");
        }
        int addrLen = sizeof(addr);
        getsockname(listenSock_, reinterpret_cast<sockaddr*>(&addr), &addrLen);
        port_ = ntohs(addr.sin_port);

        thread_ = std::thread([this]() {
            SOCKET accepted = accept(listenSock_, nullptr, nullptr);
            {
                std::lock_guard<std::mutex> lock(socketMutex_);
                if (!running_.load() || accepted == INVALID_SOCKET) {
                    if (accepted != INVALID_SOCKET) closesocket(accepted);
                    return;
                }
                clientSock_ = accepted;
                auto script = BuildHandshakeAndOneFrame();
                size_t sent = 0;
                while (sent < script.size()) {
                    int result = send(clientSock_,
                                      reinterpret_cast<const char*>(script.data() + sent),
                                      static_cast<int>(script.size() - sent), 0);
                    if (result <= 0) return;
                    sent += static_cast<size_t>(result);
                }
            }
            while (running_.load()) {
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
            }
        });
    }

    ~IdleVideoServer() {
        running_.store(false);
        {
            std::lock_guard<std::mutex> lock(socketMutex_);
            CloseSocket(clientSock_);
            CloseSocket(listenSock_);
        }
        if (thread_.joinable()) thread_.join();
        if (wsaInitialized_) WSACleanup();
    }

    int Port() const { return port_; }

private:
    static void CloseSocket(SOCKET& sock) {
        if (sock == INVALID_SOCKET) return;
        shutdown(sock, SD_BOTH);
        closesocket(sock);
        sock = INVALID_SOCKET;
    }

    SOCKET listenSock_ = INVALID_SOCKET;
    SOCKET clientSock_ = INVALID_SOCKET;
    int port_ = 0;
    std::thread thread_;
    std::atomic<bool> running_{true};
    std::mutex socketMutex_;
    bool wsaInitialized_ = false;
};

} // namespace

TEST(ScrcpyVideoClient, ConnectAndHandshakeParsesDeviceNameAndDimensions) {
    int port = StartFakeScrcpyServer(BuildHandshakeAndOneFrame());

    ScrcpyVideoClient client(port);
    client.Connect();
    client.ReadHandshake();

    EXPECT_EQ(client.DeviceName(), "test-device");
    EXPECT_EQ(client.Width(), 1280);
    EXPECT_EQ(client.Height(), 720);
}

TEST(ScrcpyVideoClient, StartReadingInvokesCallbackWithFramePayload) {
    int port = StartFakeScrcpyServer(BuildHandshakeAndOneFrame());

    ScrcpyVideoClient client(port);
    client.Connect();
    client.ReadHandshake();

    std::vector<uint8_t> received;
    bool gotFrame = false;
    client.StartReading([&](const uint8_t* data, size_t size) {
        received.assign(data, data + size);
        gotFrame = true;
    });

    for (int i = 0; i < 50 && !gotFrame; ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(20));
    }
    client.Stop();

    ASSERT_TRUE(gotFrame);
    std::vector<uint8_t> expected = {0x00, 0x00, 0x00, 0x01, 0x65, 0xAA, 0xBB};
    EXPECT_EQ(received, expected);
}

TEST(ScrcpyVideoClient, ConstructorDoesNotThrowOnValidPort) {
    // Port itself isn't validated at construction — only Connect() attempts
    // the TCP connect and can fail. This test documents that contract.
    EXPECT_NO_THROW(ScrcpyVideoClient(12345));
}

TEST(ScrcpyVideoClient, UnexpectedFrameEofSetsReadFailure) {
    int port = StartFakeScrcpyServer(BuildHandshakeAndOneFrame());
    ScrcpyVideoClient client(port);
    client.Connect();
    client.ReadHandshake();
    EXPECT_FALSE(client.LastReadFailed());
    client.StartReading([](const uint8_t*, size_t) {});

    for (int i = 0; i < 100 && !client.LastReadFailed(); ++i) {
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    EXPECT_TRUE(client.LastReadFailed());
    client.Stop();
}

TEST(ScrcpyVideoClient, IntentionalStopDoesNotSetReadFailure) {
    IdleVideoServer server;
    ScrcpyVideoClient client(server.Port());
    client.Connect();
    client.ReadHandshake();
    client.StartReading([](const uint8_t*, size_t) {});

    client.Stop();

    EXPECT_FALSE(client.LastReadFailed());
}
