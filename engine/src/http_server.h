#pragma once
#include <httplib.h>
#include <atomic>
#include <string>
#include <thread>

// Thin wrapper around one httplib::Server bound to an OS-assigned
// ephemeral port. Callers register routes on Server() before calling
// Start() — httplib does not support adding routes after Listen() begins
// accepting connections on its own thread.
class EngineHttpServer {
public:
    explicit EngineHttpServer(std::string bindAddress);
    ~EngineHttpServer();

    EngineHttpServer(const EngineHttpServer&) = delete;
    EngineHttpServer& operator=(const EngineHttpServer&) = delete;

    httplib::Server& Server();
    void Start();
    void Stop();
    int Port() const;

private:
    std::string bindAddress_;
    httplib::Server server_;
    std::thread serveThread_;
    std::atomic<int> port_{0};
};
