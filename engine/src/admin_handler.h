#pragma once
#include "peer_registry.h"
#include "scrcpy_source.h"
#include <httplib.h>

class AdminHandler {
public:
    AdminHandler(ScrcpySource& source, const PeerRegistry& registry);
    void RegisterRoutes(httplib::Server& server);

private:
    ScrcpySource& source_;
    const PeerRegistry& registry_;
};
