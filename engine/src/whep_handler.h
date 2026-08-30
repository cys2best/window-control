#pragma once

#include "peer_registry.h"
#include "whep_capability.h"

#include <httplib.h>

#include <string>
#include <vector>

class WhepHandler {
public:
    WhepHandler(PeerRegistry& registry, WhepCapabilityConfig authConfig,
                std::vector<std::string> iceServers);

    void RegisterRoutes(httplib::Server& server);

private:
    PeerRegistry& registry_;
    WhepCapabilityConfig authConfig_;
    std::vector<std::string> iceServers_;
};
