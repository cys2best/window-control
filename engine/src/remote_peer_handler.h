#pragma once
#include "peer_registry.h"
#include "scrcpy_source.h"
#include "input_router.h"
#include "whep_capability.h"
#include <httplib.h>

class RemotePeerHandler {
public:
    RemotePeerHandler(PeerRegistry& registry, ScrcpySource& source,
                      InputRouter& inputRouter, WhepCapabilityConfig authConfig);
    void RegisterRoutes(httplib::Server& server);
    void operator()(const httplib::Request& req, httplib::Response& res);

private:
    PeerRegistry& registry_;
    ScrcpySource& source_;
    InputRouter& inputRouter_;
    WhepCapabilityConfig authConfig_;
};
