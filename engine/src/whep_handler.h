#pragma once

#include "input_router.h"
#include "peer_registry.h"
#include "whep_capability.h"

#include <httplib.h>

#include <string>
#include <vector>

class WhepHandler {
public:
    WhepHandler(PeerRegistry& registry, WhepCapabilityConfig authConfig,
                std::vector<std::string> iceServers, InputRouter& inputRouter);

    void RegisterRoutes(httplib::Server& server);

private:
    PeerRegistry& registry_;
    WhepCapabilityConfig authConfig_;
    std::vector<std::string> iceServers_;
    InputRouter& inputRouter_;
};
