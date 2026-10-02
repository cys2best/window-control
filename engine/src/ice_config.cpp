#include "ice_config.h"
#include <regex>
#include <stdexcept>
#if defined(_WIN32)
#include <winsock2.h>
#include <ws2tcpip.h>
#else
#include <arpa/inet.h>
#endif

namespace {
bool ValidText(const std::string& value) {
    for (unsigned char c : value) if (c < 32 || c == 127) return false;
    return true;
}
}

rtc::Configuration BuildIceConfiguration(const std::vector<IceServerSpec>& specs) {
    if (specs.size() > 16) throw std::invalid_argument("too many ICE server specifications");
    static const std::regex syntax(
        R"(^(stun|turn|turns):(\[[0-9a-fA-F:.]+\]|[A-Za-z0-9][A-Za-z0-9.-]*)(?::([0-9]{1,5}))?(?:\?transport=(udp|tcp))?$)");
    rtc::Configuration config;
    size_t count = 0;
    for (const auto& spec : specs) {
        if (spec.urls.empty() || spec.username.size() > 1024 || spec.credential.size() > 1024 ||
            !ValidText(spec.username) || !ValidText(spec.credential)) {
            throw std::invalid_argument("invalid ICE server credentials or empty URL list");
        }
        for (const auto& url : spec.urls) {
            std::smatch match;
            if (++count > 32 || url.size() > 2048 || !std::regex_match(url, match, syntax)) {
                throw std::invalid_argument("invalid ICE server URL or URL limit exceeded");
            }
            const std::string scheme = match[1];
            std::string host = match[2];
            const std::string transport = match[4];
            if (host.front() == '[') {
                host = host.substr(1, host.size() - 2);
                in6_addr address{};
                if (inet_pton(AF_INET6, host.c_str(), &address) != 1) {
                    throw std::invalid_argument("invalid ICE IPv6 literal");
                }
            } else {
                if (host.size() > 253) throw std::invalid_argument("ICE hostname is too long");
                size_t start = 0;
                while (start < host.size()) {
                    auto end = host.find('.', start);
                    if (end == std::string::npos) end = host.size();
                    const auto length = end - start;
                    if (!length || length > 63 || host[start] == '-' || host[end - 1] == '-') {
                        throw std::invalid_argument("invalid ICE hostname");
                    }
                    start = end + 1;
                }
                if (host.back() == '.') throw std::invalid_argument("invalid ICE hostname");
            }
            unsigned port = scheme == "turns" ? 5349 : 3478;
            if (match[3].matched) port = static_cast<unsigned>(std::stoul(match[3]));
            if (!port || port > 65535) throw std::invalid_argument("invalid ICE port");
            if (scheme == "stun") {
                if (!spec.username.empty() || !spec.credential.empty() ||
                    (!transport.empty() && transport != "udp")) {
                    throw std::invalid_argument("STUN requires UDP and no credentials");
                }
                config.iceServers.emplace_back(host, static_cast<uint16_t>(port));
            } else {
                if (spec.username.empty() || spec.credential.empty() ||
                    (scheme == "turns" && transport == "udp")) {
                    throw std::invalid_argument("TURN requires credentials and a supported transport");
                }
                const auto relayType = scheme == "turns" ? rtc::IceServer::RelayType::TurnTls
                    : transport == "tcp" ? rtc::IceServer::RelayType::TurnTcp
                    : rtc::IceServer::RelayType::TurnUdp;
                config.iceServers.emplace_back(host, static_cast<uint16_t>(port),
                                               spec.username, spec.credential, relayType);
            }
        }
    }
    config.iceTransportPolicy = rtc::TransportPolicy::All;
    // v0.21.1 libnice gathers IPv4 and IPv6 by default; no family selector
    // exists in rtc::Configuration. Leave addresses unrestricted.
    return config;
}
