#pragma once

#include "peer_registry.h"
#include "scrcpy_source.h"
#include "input_router.h"
#include "engine_config.h"

class RemotePeerHandler {
public:
    RemotePeerHandler(PeerRegistry& registry, ScrcpySource& source, InputRouter& inputRouter, const EngineConfig::AuthConfig& authConfig);
    ~RemotePeerHandler();

    void operator()(const httplib::Request& req, httplib::Response& res);

private:
    PeerRegistry& registry_;
    ScrcpySource& source_;
    InputRouter& inputRouter_;
    EngineConfig::AuthConfig authConfig_;
};
