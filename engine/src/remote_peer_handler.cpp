#include "remote_peer_handler.h"
#include "ice_config.h"
#include <nlohmann/json.hpp>
#include <openssl/rand.h>
#include <array>
#include <chrono>
#include <regex>
#include <stdexcept>
#include <utility>

using json = nlohmann::json;
namespace {
using Clock = std::chrono::steady_clock;
std::string Bearer(const httplib::Request& req) {
    const auto value = req.get_header_value("Authorization");
    return value.rfind("Bearer ", 0) == 0 ? value.substr(7) : "";
}
std::string PeerId() {
    std::array<unsigned char, 16> bytes{};
    if (RAND_bytes(bytes.data(), static_cast<int>(bytes.size())) != 1) {
        throw std::runtime_error("peer id entropy unavailable");
    }
    constexpr char hex[] = "0123456789abcdef";
    std::string result;
    for (auto byte : bytes) { result += hex[byte >> 4]; result += hex[byte & 15]; }
    return result;
}
bool UnsignedInteger(const json& value) {
    return value.is_number_unsigned() || (value.is_number_integer() && value.get<std::int64_t>() >= 0);
}
}

RemotePeerHandler::RemotePeerHandler(PeerRegistry& registry, ScrcpySource& source,
    InputRouter& inputRouter, WhepCapabilityConfig authConfig)
    : registry_(registry), source_(source), inputRouter_(inputRouter),
      authConfig_(std::move(authConfig)) {}

void RemotePeerHandler::RegisterRoutes(httplib::Server& server) {
    server.Post("/admin/remote-peers", [this](const auto& req, auto& res) { (*this)(req, res); });
    server.Delete(R"(/admin/remote-peers/([a-f0-9]{32}))",
                  [this](const auto& req, auto& res) { (*this)(req, res); });
}

void RemotePeerHandler::operator()(const httplib::Request& req, httplib::Response& res) {
    const auto started = Clock::now();
    if (req.remote_addr != "127.0.0.1" && req.remote_addr != "::1") {
        res.status = 403; return;
    }
    const auto token = Bearer(req);
    // Unlike local WHEP's optional authentication, remote routes always require
    // a configured, PC-generated capability, even on the loopback listener.
    if (authConfig_.secret.empty() || !ValidateWhepCapability(authConfig_, token)) {
        res.status = 401; return;
    }
    if (req.method == "DELETE") {
        const auto id = req.path.substr(req.path.rfind('/') + 1);
        if (!std::regex_match(id, std::regex("[a-f0-9]{32}"))) { res.status = 404; return; }
        // Local peer ids remain outside the remote deletion surface.
        if (registry_.Find(id) && !registry_.PublicGeneration(id)) { res.status = 404; return; }
        std::shared_ptr<PeerSession> retired;
        res.status = registry_.CancelPublicAttempt(id, retired) ? 204 : 503;
        if (retired) retired->Close();
        return;
    }
    if (req.method != "POST") { res.status = 405; return; }
    if (req.body.size() > 1024 * 1024) { res.status = 413; return; }

    std::uint64_t generation;
    std::string offer;
    std::string id;
    int timeoutMs;
    rtc::Configuration config;
    try {
        const auto body = json::parse(req.body);
        if (!body.is_object() || !body.contains("session_id") || !body["session_id"].is_string() ||
            !body.contains("generation") || !UnsignedInteger(body["generation"]) ||
            !body.contains("offer") || !body["offer"].is_string() ||
            !body.contains("timeout_ms") || !UnsignedInteger(body["timeout_ms"]) ||
            !body.contains("ice_servers") || !body["ice_servers"].is_array()) {
            throw std::invalid_argument("invalid remote peer fields");
        }
        const auto sessionId = body["session_id"].get<std::string>();
        if (!std::regex_match(sessionId, std::regex("[A-Za-z0-9_-]{1,128}"))) {
            throw std::invalid_argument("invalid session id");
        }
        if (body.contains("peer_id")) {
            if (!body["peer_id"].is_string()) throw std::invalid_argument("invalid peer id");
            id = body["peer_id"].get<std::string>();
            if (!std::regex_match(id, std::regex("[a-f0-9]{32}"))) {
                throw std::invalid_argument("invalid peer id");
            }
        }
        generation = body["generation"].get<std::uint64_t>();
        const auto timeout = body["timeout_ms"].get<std::uint64_t>();
        if (!timeout || timeout > 30000) throw std::invalid_argument("invalid negotiation timeout");
        timeoutMs = static_cast<int>(timeout);
        offer = body["offer"].get<std::string>();
        if (offer.empty() || offer.size() > 128 * 1024) throw std::invalid_argument("invalid offer size");
        if (body["ice_servers"].size() > 16) throw std::invalid_argument("too many ICE servers");
        std::vector<IceServerSpec> specs;
        for (const auto& server : body["ice_servers"]) {
            if (!server.is_object() || !server.contains("urls") || !server["urls"].is_array() ||
                server["urls"].size() > 32) throw std::invalid_argument("invalid ICE URLs");
            specs.push_back({server["urls"].get<std::vector<std::string>>(),
                             server.value("username", std::string{}),
                             server.value("credential", std::string{})});
        }
        config = BuildIceConfiguration(specs);
    } catch (const std::exception&) {
        res.status = 400; return;
    }
    if (!source_.WithGeneration(generation, [] {})) { res.status = 409; return; }
    const auto deadline = started + std::chrono::milliseconds(timeoutMs);
    auto canceled = [&] { return req.is_connection_closed() || Clock::now() >= deadline; };
    if (canceled()) { res.status = 408; return; }

    std::shared_ptr<PeerSession> session;
    std::vector<std::shared_ptr<PeerSession>> retired;
    bool adopted = false;
    try {
        if (id.empty()) id = PeerId();
        session = std::make_shared<PeerSession>(id, std::move(config));
        inputRouter_.AttachToPeer(*session);
        const auto remaining = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - Clock::now());
        if (remaining.count() <= 0 || canceled()) { res.status = 408; }
        else {
            const auto answer = session->AnswerOffer(offer, remaining, canceled);
            if (answer.size() > 128 * 1024) throw std::runtime_error("gathered answer exceeds SDP limit");
            // Serialize generation validation with source retire/install, and
            // adoption with peer close/failure. No teardown under either lock.
            bool current = source_.WithGeneration(generation, [&] {
                res.status = 502;
                adopted = session->WithActive([&] {
                    if (canceled()) { res.status = 408; return false; }
                    if (!ValidateWhepCapability(authConfig_, token)) { res.status = 401; return false; }
                    return registry_.AdoptPublic(session, generation, retired);
                });
                if (adopted) res.status = 201;
            });
            if (!current) res.status = 409;
            if (adopted) {
                res.set_content(json{{"peer_id", id}, {"answer", answer}, {"generation", generation}}.dump(),
                                "application/json");
            }
        }
    } catch (const std::exception&) {
        res.status = canceled() ? 408 : 502;
        if (adopted && session) {
            auto victim = registry_.RemovePublic(session->Id(), generation);
            if (victim) retired.push_back(std::move(victim));
            adopted = false;
        }
    }
    for (const auto& peer : retired) peer->Close();
    if (!adopted && session) session->Close();
}
