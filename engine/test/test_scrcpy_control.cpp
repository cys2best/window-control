#include <gtest/gtest.h>
#include "scrcpy_control.h"
#include <winsock2.h>
#include <ws2tcpip.h>
#include <thread>
#include <vector>

#pragma comment(lib, "ws2_32.lib")

namespace {

// Starts a listening socket, accepts one connection, reads exactly
// `expectedBytes` bytes from it into `outReceived`, then closes. Returns
// the bound port. The read runs on a background thread so the test can
// connect+send from the main thread without deadlocking.
int StartCapturingServer(size_t expectedBytes, std::vector<uint8_t>& outReceived,
                          std::atomic<bool>& doneFlag) {
    WSADATA wsaData;
    WSAStartup(MAKEWORD(2, 2), &wsaData);

    SOCKET listenSock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port = 0;
    bind(listenSock, reinterpret_cast<sockaddr*>(&addr), sizeof(addr));
    listen(listenSock, 1);

    int addrLen = sizeof(addr);
    getsockname(listenSock, reinterpret_cast<sockaddr*>(&addr), &addrLen);
    int port = ntohs(addr.sin_port);

    std::thread([listenSock, expectedBytes, &outReceived, &doneFlag]() {
        SOCKET client = accept(listenSock, nullptr, nullptr);
        outReceived.resize(expectedBytes);
        size_t received = 0;
        while (received < expectedBytes) {
            int r = recv(client, reinterpret_cast<char*>(outReceived.data() + received),
                         static_cast<int>(expectedBytes - received), 0);
            if (r <= 0) break;
            received += static_cast<size_t>(r);
        }
        doneFlag = true;
        closesocket(client);
        closesocket(listenSock);
    }).detach();

    return port;
}

} // namespace

TEST(ScrcpyControlClient, SendTouchProducesExact32ByteMessage) {
    std::vector<uint8_t> received;
    std::atomic<bool> done{false};
    int port = StartCapturingServer(32, received, done);

    ScrcpyControlClient client(port);
    client.Connect();
    // action=DOWN, nx=0.5, ny=0.5, screen 1280x720, pointerId=0
    client.SendTouch(ScrcpyControlClient::ACTION_DOWN, 0.5, 0.5, 1280, 720, 0);

    for (int i = 0; i < 50 && !done; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(20));
    ASSERT_TRUE(done);
    ASSERT_EQ(received.size(), 32u);

    EXPECT_EQ(received[0], 0x02); // type: INJECT_TOUCH_EVENT
    EXPECT_EQ(received[1], 0x00); // action: DOWN
    // pointerId (u64 BE) at [2..10) == 0
    for (int i = 2; i < 10; ++i) EXPECT_EQ(received[i], 0);
    // x = 0.5 * 1280 = 640 (i32 BE) at [10..14)
    uint32_t x = (received[10] << 24) | (received[11] << 16) | (received[12] << 8) | received[13];
    EXPECT_EQ(x, 640u);
    // y = 0.5 * 720 = 360 (i32 BE) at [14..18)
    uint32_t y = (received[14] << 24) | (received[15] << 16) | (received[16] << 8) | received[17];
    EXPECT_EQ(y, 360u);
    // screenWidth (u16 BE) at [18..20)
    uint16_t w = (received[18] << 8) | received[19];
    EXPECT_EQ(w, 1280u);
    // screenHeight (u16 BE) at [20..22)
    uint16_t h = (received[20] << 8) | received[21];
    EXPECT_EQ(h, 720u);
    // pressure (u16 BE) at [22..24) == 0xffff for DOWN
    uint16_t pressure = (received[22] << 8) | received[23];
    EXPECT_EQ(pressure, 0xffffu);
}

TEST(ScrcpyControlClient, SendTouchActionUpHasZeroPressure) {
    std::vector<uint8_t> received;
    std::atomic<bool> done{false};
    int port = StartCapturingServer(32, received, done);

    ScrcpyControlClient client(port);
    client.Connect();
    client.SendTouch(ScrcpyControlClient::ACTION_UP, 0.5, 0.5, 1280, 720, 0);

    for (int i = 0; i < 50 && !done; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(20));
    ASSERT_TRUE(done);
    uint16_t pressure = (received[22] << 8) | received[23];
    EXPECT_EQ(pressure, 0u);
}

TEST(ScrcpyControlClient, SendKeycodeProducesDownThenUp14ByteMessages) {
    std::vector<uint8_t> received;
    std::atomic<bool> done{false};
    int port = StartCapturingServer(28, received, done); // 14 bytes down + 14 bytes up

    ScrcpyControlClient client(port);
    client.Connect();
    client.SendKeycode(4); // KEYCODE_BACK

    for (int i = 0; i < 50 && !done; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(20));
    ASSERT_TRUE(done);
    ASSERT_EQ(received.size(), 28u);

    // First message (down): type=0x00, action=0
    EXPECT_EQ(received[0], 0x00);
    EXPECT_EQ(received[1], 0x00);
    // Second message (up): type=0x00, action=1
    EXPECT_EQ(received[14], 0x00);
    EXPECT_EQ(received[15], 0x01);
}
