// Wire protocol matches src/server/scrcpy_session.py exactly (see that
// file's module docstring and _stream_loop method):
//   1. Connect TCP to 127.0.0.1:<port> (the port from `adb forward`).
//   2. Read 1-byte dummy (sent by scrcpy-server immediately after accepting
//      the video connection).
//   3. (Caller must now connect the control socket — see Task 6 — so
//      scrcpy-server's accept() for control unblocks and it proceeds to
//      send device_meta + codec header on THIS video socket.)
//   4. Read 64-byte device name (zero-padded UTF-8).
//   5. Read 12-byte meta: codec_id (u32 BE) + width (u32 BE) + height (u32 BE).
//   6. Frame loop: 12-byte header (u64 BE pts_flags + u32 BE size) + payload.
#include "scrcpy_video.h"
#include <winsock2.h>
#include <ws2tcpip.h>
#include <cstring>
#include <vector>
#include <iostream>
#include <exception>

#pragma comment(lib, "ws2_32.lib")

namespace {

// Sanity bound on the wire `size` field — it comes from an untrusted/
// unvalidated socket read, so a corrupt stream must not trigger an
// unbounded allocation.
constexpr uint32_t kMaxFrameBytes = 8 * 1024 * 1024;

bool RecvAll(SOCKET sock, uint8_t* buf, size_t n) {
    size_t received = 0;
    while (received < n) {
        int r = recv(sock, reinterpret_cast<char*>(buf + received), static_cast<int>(n - received), 0);
        if (r <= 0) return false;
        received += static_cast<size_t>(r);
    }
    return true;
}

uint32_t ReadU32BE(const uint8_t* p) {
    return (static_cast<uint32_t>(p[0]) << 24) | (static_cast<uint32_t>(p[1]) << 16) |
           (static_cast<uint32_t>(p[2]) << 8) | static_cast<uint32_t>(p[3]);
}

uint64_t ReadU64BE(const uint8_t* p) {
    uint64_t v = 0;
    for (int i = 0; i < 8; ++i) v = (v << 8) | p[i];
    return v;
}

} // namespace

struct ScrcpyVideoClient::Impl {
    int port;
    SOCKET sock = INVALID_SOCKET;
    std::string deviceName;
    int width = 0;
    int height = 0;
    std::atomic<bool> running{false};
    std::thread readThread;
    NaluCallback onNalu;
    bool wsaInitialized = false;

    explicit Impl(int p) : port(p) {
        WSADATA wsaData;
        wsaInitialized = (WSAStartup(MAKEWORD(2, 2), &wsaData) == 0);
    }

    ~Impl() {
        if (sock != INVALID_SOCKET) closesocket(sock);
        if (wsaInitialized) WSACleanup();
    }
};

ScrcpyVideoClient::ScrcpyVideoClient(int port) : impl_(std::make_unique<Impl>(port)) {}

ScrcpyVideoClient::~ScrcpyVideoClient() {
    Stop();
}

void ScrcpyVideoClient::Connect() {
    std::cerr << "[debug] video: socket()..." << std::endl;
    impl_->sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (impl_->sock == INVALID_SOCKET) {
        throw std::runtime_error("ScrcpyVideoClient: socket() failed");
    }

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(static_cast<uint16_t>(impl_->port));
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);

    std::cerr << "[debug] video: connect() on port " << impl_->port << "..." << std::endl;
    if (connect(impl_->sock, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) != 0) {
        throw std::runtime_error("ScrcpyVideoClient: connect() failed on port " + std::to_string(impl_->port));
    }
    std::cerr << "[debug] video: connected, reading dummy byte..." << std::endl;

    uint8_t dummy;
    if (!RecvAll(impl_->sock, &dummy, 1)) {
        throw std::runtime_error("ScrcpyVideoClient: failed to read dummy byte after connect");
    }
    std::cerr << "[debug] video: dummy byte received" << std::endl;
}

void ScrcpyVideoClient::ReadHandshake() {
    std::cerr << "[debug] video: reading 64-byte device name..." << std::endl;
    uint8_t nameBuf[64];
    if (!RecvAll(impl_->sock, nameBuf, 64)) {
        throw std::runtime_error("ScrcpyVideoClient: handshake truncated reading device name");
    }
    // Trim trailing zero padding.
    size_t len = 0;
    while (len < 64 && nameBuf[len] != 0) ++len;
    impl_->deviceName.assign(reinterpret_cast<char*>(nameBuf), len);
    std::cerr << "[debug] video: device name = " << impl_->deviceName << ", reading 12-byte meta..." << std::endl;

    uint8_t meta[12];
    if (!RecvAll(impl_->sock, meta, 12)) {
        throw std::runtime_error("ScrcpyVideoClient: handshake truncated reading codec/size meta");
    }
    // meta[0:4] = codec_id, unused by this client (scrcpy always uses H264
    // per the launch args `_start_server` passes — see scrcpy_session.py).
    impl_->width = static_cast<int>(ReadU32BE(meta + 4));
    impl_->height = static_cast<int>(ReadU32BE(meta + 8));
}

void ScrcpyVideoClient::StartReading(NaluCallback onNalu) {
    impl_->onNalu = std::move(onNalu);
    impl_->running = true;
    impl_->readThread = std::thread([this]() {
        uint8_t header[12];
        while (impl_->running.load()) {
            if (!RecvAll(impl_->sock, header, 12)) break;
            uint64_t ptsFlags = ReadU64BE(header); // unused for RTP repacketization in this PoC
            (void)ptsFlags;
            uint32_t size = ReadU32BE(header + 8);
            if (size > kMaxFrameBytes) break; // stream desync — treat as fatal, same as any other read error

            std::vector<uint8_t> payload(size);
            if (size > 0 && !RecvAll(impl_->sock, payload.data(), size)) break;

            if (impl_->onNalu) {
                try {
                    impl_->onNalu(payload.data(), payload.size());
                } catch (const std::exception& e) {
                    std::cerr << "ScrcpyVideoClient: onNalu callback threw: " << e.what() << std::endl;
                }
            }
        }
    });
}

void ScrcpyVideoClient::Stop() {
    if (!impl_->running.exchange(false)) {
        if (impl_->readThread.joinable()) impl_->readThread.join();
        return;
    }
    if (impl_->sock != INVALID_SOCKET) {
        shutdown(impl_->sock, SD_BOTH); // unblocks the read thread's recv()
    }
    if (impl_->readThread.joinable()) impl_->readThread.join();
}

std::string ScrcpyVideoClient::DeviceName() const { return impl_->deviceName; }
int ScrcpyVideoClient::Width() const { return impl_->width; }
int ScrcpyVideoClient::Height() const { return impl_->height; }
