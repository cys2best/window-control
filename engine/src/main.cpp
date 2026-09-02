// engine/src/main.cpp
#include "admin_handler.h"
#include "engine_config.h"
#include "http_server.h"
#include "input_router.h"
#include "peer_registry.h"
#include "public_signaling.h"
#include "ready_record.h"
#include "scrcpy_source.h"
#include "signaling_client.h"
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
                     "ENGINE_LOCAL_ICE_SERVERS, ENGINE_SIGNALING_URL, "
                     "ENGINE_SIGNALING_TOKEN, ENGINE_PUBLIC_ICE_SERVERS\n";
        return 1;
    }

    std::signal(SIGINT, OnSigint);

    try {
        std::string instanceName = argv[1];
        int scrcpyPort = std::stoi(argv[2]);
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
        adminServer.Start();

        std::unique_ptr<PublicSignalingBridge> publicBridge;
        // Destroy the transport first on exceptional exits so its callback
        // cannot outlive the bridge object it captures.
        std::unique_ptr<SignalingClient> signaling;
        std::string signalingUrl = GetEnvOrEmpty("ENGINE_SIGNALING_URL");
        if (!signalingUrl.empty()) {
            std::string signalingToken = GetEnvOrEmpty("ENGINE_SIGNALING_TOKEN");
            signaling = std::make_unique<SignalingClient>(
                signalingUrl, instanceName, "engine", signalingToken);
            auto publicIceServers = ParseCommaSeparatedList(GetEnvOrEmpty("ENGINE_PUBLIC_ICE_SERVERS"));
            publicBridge = std::make_unique<PublicSignalingBridge>(*signaling, registry, publicIceServers, inputRouter);
            publicBridge->Start();
        }

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
        if (signaling) signaling->Disconnect();
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
