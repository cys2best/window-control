#include "ice_config.h"
#include <stdexcept>
#include <regex>

rtc::Configuration BuildIceConfiguration(const std::vector<IceServerSpec>& specs) {
    rtc::Configuration config;
    for (const auto& spec : specs) {
        if (spec.urls.empty()) continue;
        
        // Use the library's credential-aware constructor if username/credential are provided
        if (!spec.username.empty() || !spec.credential.empty()) {
            config.iceServers.emplace_back(rtc::IceServer(spec.urls[0], spec.username, spec.credential));
        } else {
            config.iceServers.emplace_back(rtc::IceServer(spec.urls[0]));
        }
    }
    config.iceTransportPolicy = rtc::TransportPolicy::All;
    config.enableIceUdpMux = true;
    return config;
}
