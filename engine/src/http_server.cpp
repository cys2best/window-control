#include "http_server.h"
#include <stdexcept>

EngineHttpServer::EngineHttpServer(std::string bindAddress)
    : bindAddress_(std::move(bindAddress)) {}

EngineHttpServer::~EngineHttpServer() { Stop(); }

httplib::Server& EngineHttpServer::Server() { return server_; }

void EngineHttpServer::Start() {
    // bind_to_any_port + listen_after_bind splits port selection from the
    // blocking accept loop, so Port() is valid the instant Start() returns
    // instead of racing the background thread's own bind() call.
    int bound = server_.bind_to_any_port(bindAddress_.c_str());
    if (bound <= 0) {
        throw std::runtime_error("EngineHttpServer: bind_to_any_port failed on " + bindAddress_);
    }
    port_.store(bound);
    serveThread_ = std::thread([this]() { server_.listen_after_bind(); });
    // cpp-httplib 0.19.0's stop() only closes the listening socket after
    // listen_after_bind() has set is_running_. Without this barrier, an
    // immediate Stop() can observe false, do nothing, and then wait forever
    // while the server thread enters its accept loop.
    server_.wait_until_ready();
}

void EngineHttpServer::Stop() {
    if (!serveThread_.joinable()) return;
    server_.stop();
    serveThread_.join();
}

int EngineHttpServer::Port() const { return port_.load(); }
