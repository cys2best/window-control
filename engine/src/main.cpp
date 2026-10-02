// engine/src/main.cpp
#include "admin_handler.h"
#include "engine_config.h"
#include "http_server.h"
#include "input_router.h"
#include "peer_registry.h"
#include "ready_record.h"
#include "remote_peer_handler.h"
#include "scrcpy_source.h"
#include "send_pacer.h"
#include "whep_capability.h"
#include "whep_handler.h"
#include <atomic>
#include <chrono>
#include <csignal>
#include <cstdlib>
#include <iostream>
#include <thread>

#if defined(_WIN32)
#include <process.h>
#define GetProcId() _getpid()
#else
#include <unistd.h>
#define GetProcId() getpid()
#endif

std::atomic<bool> g_running{true};
void OnSigint(int) { g_running = false; }

namespace {
std::string GetEnvOrEmpty(const char* name) {
    const char* value = std::getenv(name);
    return value ? std::string(value) : std::string();
}

class InputPeerShutdownGuard {
public:
    InputPeerShutdownGuard(PeerRegistry& registry, InputRouter& inputRouter)
        : registry_(registry), inputRouter_(inputRouter) {}

    ~InputPeerShutdownGuard() {
        try {
            Run();
        } catch (...) {
            // Destructor cleanup is best-effort; the explicit normal-path Run
            // reports failures through main's exception handling.
        }
    }

    void Run() {
        if (complete_) return;
        inputRouter_.ShutdownPeers(registry_);
        complete_ = true;
    }

private:
    PeerRegistry& registry_;
    InputRouter& inputRouter_;
    bool complete_ = false;
};
}

int main(int argc, char** argv) {
    if (argc < 3) {
        std::cerr << "Usage: engine.exe <instance_name> <scrcpy_port>\n"
                     "Environment: ENGINE_WHEP_CAPABILITY_SECRET, "
                     "ENGINE_LOCAL_ICE_SERVERS, ENGINE_VIDEO_BIT_RATE\n";
        return 1;
    }

    std::signal(SIGINT, OnSigint);

    try {
        std::string instanceName = argv[1];
        int scrcpyPort = std::stoi(argv[2]);
        // Encoder bitrate of the first source; each reconnect updates it.
        // Unset or unreadable leaves pacing off.
        const std::string videoBitRate = GetEnvOrEmpty("ENGINE_VIDEO_BIT_RATE");
        if (!videoBitRate.empty()) {
            try {
                video_target::SetBitsPerSecond(std::stod(videoBitRate));
            } catch (const std::exception&) {
                std::cerr << "[engine] ignoring unreadable ENGINE_VIDEO_BIT_RATE" << std::endl;
            }
        }

        PeerRegistry registry;
        ScrcpySource source(registry);
        source.ConnectInitial(scrcpyPort);

        InputRouter inputRouter(source);
        InputPeerShutdownGuard inputPeerShutdown(registry, inputRouter);

        WhepCapabilityConfig whepAuth{GetEnvOrEmpty("ENGINE_WHEP_CAPABILITY_SECRET"), instanceName};
        auto localIceServers = ParseCommaSeparatedList(GetEnvOrEmpty("ENGINE_LOCAL_ICE_SERVERS"));

        EngineHttpServer whepServer("0.0.0.0");
        WhepHandler whepHandler(registry, whepAuth, localIceServers, inputRouter);
        whepHandler.RegisterRoutes(whepServer.Server());
        whepServer.Start();

        EngineHttpServer adminServer("127.0.0.1");
        AdminHandler adminHandler(source, registry);
        adminHandler.RegisterRoutes(adminServer.Server());
        RemotePeerHandler remotePeerHandler(registry, source, inputRouter, whepAuth);
        remotePeerHandler.RegisterRoutes(adminServer.Server());
        adminServer.Start();

        auto status = source.Status();
        std::string ready = BuildReadyRecord(
            instanceName, GetProcId(), whepServer.Port(), adminServer.Port(),
            status.generation, status.width, status.height);
        std::cout << ready << std::endl;

        auto lastHousekeeping = std::chrono::steady_clock::now();
        while (g_running) {
            std::this_thread::sleep_for(std::chrono::milliseconds(200));
            auto now = std::chrono::steady_clock::now();
            if (now - lastHousekeeping >= std::chrono::seconds(1)) {
                registry.ReapDeadAndStalePeers();
                lastHousekeeping = now;
            }
        }

        whepServer.Stop();
        adminServer.Stop();
        inputPeerShutdown.Run();
        std::cout << "Stopped.\n";
        return 0;
    } catch (const std::exception& e) {
        std::cerr << "[FATAL] unhandled exception: " << e.what() << std::endl;
        return 1;
    } catch (...) {
        std::cerr << "[FATAL] unhandled non-std::exception (unknown type)" << std::endl;
        return 1;
    }
}
