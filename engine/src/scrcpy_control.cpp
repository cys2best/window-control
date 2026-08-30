// engine/src/scrcpy_control.cpp
// Binary control message formats match src/server/scrcpy_session.py's
// ScrcpyControl class exactly (INJECT_TOUCH_EVENT and INJECT_KEYCODE, both
// big-endian, scrcpy 3.x wire format).
#include "scrcpy_control.h"
#include <winsock2.h>
#include <ws2tcpip.h>
#include <stdexcept>
#include <string>
#include <vector>
#include <mutex>
#include <iostream>
#include <atomic>

#pragma comment(lib, "ws2_32.lib")

namespace {

void PushU8(std::vector<uint8_t>& buf, uint8_t v) { buf.push_back(v); }

void PushU16BE(std::vector<uint8_t>& buf, uint16_t v) {
    buf.push_back(static_cast<uint8_t>((v >> 8) & 0xFF));
    buf.push_back(static_cast<uint8_t>(v & 0xFF));
}

void PushU32BE(std::vector<uint8_t>& buf, uint32_t v) {
    for (int i = 3; i >= 0; --i) buf.push_back(static_cast<uint8_t>((v >> (i * 8)) & 0xFF));
}

void PushU64BE(std::vector<uint8_t>& buf, uint64_t v) {
    for (int i = 7; i >= 0; --i) buf.push_back(static_cast<uint8_t>((v >> (i * 8)) & 0xFF));
}

} // namespace

struct ScrcpyControlClient::Impl {
    int port;
    SOCKET sock = INVALID_SOCKET;
    mutable std::mutex sendMutex;
    std::atomic<bool> lastSendFailed{false};
    bool wsaInitialized = false;

    explicit Impl(int p) : port(p) {
        WSADATA wsaData;
        wsaInitialized = (WSAStartup(MAKEWORD(2, 2), &wsaData) == 0);
    }

    ~Impl() {
        if (sock != INVALID_SOCKET) closesocket(sock);
        if (wsaInitialized) WSACleanup();
    }

    void Send(const std::vector<uint8_t>& msg) {
        std::lock_guard<std::mutex> lock(sendMutex);
        if (sock == INVALID_SOCKET) {
            lastSendFailed.store(true);
            return;
        }

        size_t sent = 0;
        while (sent < msg.size()) {
            int result = send(
                sock,
                reinterpret_cast<const char*>(msg.data() + sent),
                static_cast<int>(msg.size() - sent),
                0);
            if (result == SOCKET_ERROR || result == 0) {
                lastSendFailed.store(true);
                shutdown(sock, SD_BOTH);
                closesocket(sock);
                sock = INVALID_SOCKET;
                return;
            }
            sent += static_cast<size_t>(result);
        }
    }
};

ScrcpyControlClient::ScrcpyControlClient(int port) : impl_(std::make_unique<Impl>(port)) {}

ScrcpyControlClient::~ScrcpyControlClient() = default;

void ScrcpyControlClient::Connect() {
    {
        std::lock_guard<std::mutex> lock(impl_->sendMutex);
        if (impl_->sock != INVALID_SOCKET) {
            shutdown(impl_->sock, SD_BOTH);
            closesocket(impl_->sock);
            impl_->sock = INVALID_SOCKET;
        }
    }

    std::cerr << "[debug] control: socket()..." << std::endl;
    SOCKET newSock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (newSock == INVALID_SOCKET) {
        throw std::runtime_error("ScrcpyControlClient: socket() failed");
    }

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(static_cast<uint16_t>(impl_->port));
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);

    std::cerr << "[debug] control: connect() on port " << impl_->port << "..." << std::endl;
    if (connect(newSock, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
        closesocket(newSock);
        throw std::runtime_error("ScrcpyControlClient: connect() failed on port " + std::to_string(impl_->port));
    }
    std::cerr << "[debug] control: connected" << std::endl;

    int flag = 1;
    setsockopt(newSock, IPPROTO_TCP, TCP_NODELAY, reinterpret_cast<const char*>(&flag), sizeof(flag));

    std::lock_guard<std::mutex> lock(impl_->sendMutex);
    if (impl_->sock != INVALID_SOCKET) closesocket(impl_->sock);
    impl_->sock = newSock;
}

void ScrcpyControlClient::SendTouch(uint8_t action, double nx, double ny, int screenWidth, int screenHeight, uint64_t pointerId) {
    int32_t x = static_cast<int32_t>(nx * screenWidth);
    int32_t y = static_cast<int32_t>(ny * screenHeight);
    uint16_t pressure = (action != ACTION_UP) ? 0xffff : 0;

    std::vector<uint8_t> msg;
    msg.reserve(32);
    PushU8(msg, 0x02); // type: INJECT_TOUCH_EVENT
    PushU8(msg, action);
    PushU64BE(msg, pointerId);
    PushU32BE(msg, static_cast<uint32_t>(x));
    PushU32BE(msg, static_cast<uint32_t>(y));
    PushU16BE(msg, static_cast<uint16_t>(screenWidth & 0xffff));
    PushU16BE(msg, static_cast<uint16_t>(screenHeight & 0xffff));
    PushU16BE(msg, pressure);
    PushU32BE(msg, 0); // actionButton
    PushU32BE(msg, 0); // buttons

    impl_->Send(msg);
}

void ScrcpyControlClient::SendKeycode(int32_t keycode) {
    for (uint8_t action : {static_cast<uint8_t>(0), static_cast<uint8_t>(1)}) { // down, up
        std::vector<uint8_t> msg;
        msg.reserve(14);
        PushU8(msg, 0x00); // type: INJECT_KEYCODE
        PushU8(msg, action);
        PushU32BE(msg, static_cast<uint32_t>(keycode));
        PushU32BE(msg, 0); // repeat
        PushU32BE(msg, 0); // metaState
        impl_->Send(msg);
    }
}

void ScrcpyControlClient::RequestIdr() {
    std::vector<uint8_t> msg;
    msg.reserve(1);
    PushU8(msg, 0x11); // type: TYPE_RESET_VIDEO — bodyless, requests a fresh IDR
    impl_->Send(msg);
}

bool ScrcpyControlClient::IsConnected() const {
    std::lock_guard<std::mutex> lock(impl_->sendMutex);
    return impl_->sock != INVALID_SOCKET;
}

bool ScrcpyControlClient::LastSendFailed() const {
    return impl_->lastSendFailed.load();
}

void ScrcpyControlClient::ResetSendFailureFlag() {
    impl_->lastSendFailed.store(false);
}
