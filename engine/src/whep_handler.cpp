#include "whep_handler.h"

#include <openssl/rand.h>

#include <array>
#include <stdexcept>
#include <string_view>
#include <utility>

namespace {

std::string ExtractBearerToken(const httplib::Request& request) {
    const auto authorization = request.headers.find("Authorization");
    if (authorization == request.headers.end()) return "";

    constexpr std::string_view kBearerPrefix = "Bearer ";
    if (authorization->second.rfind(kBearerPrefix, 0) != 0) return "";
    return authorization->second.substr(kBearerPrefix.size());
}

std::string GenerateUnguessableId() {
    std::array<unsigned char, 16> randomBytes{};
    if (RAND_bytes(randomBytes.data(), static_cast<int>(randomBytes.size())) != 1) {
        throw std::runtime_error("OpenSSL could not obtain session-id entropy");
    }

    constexpr char kHex[] = "0123456789abcdef";
    std::string id;
    id.reserve(randomBytes.size() * 2);
    for (unsigned char byte : randomBytes) {
        id.push_back(kHex[byte >> 4]);
        id.push_back(kHex[byte & 0x0f]);
    }
    return id;
}

void ApplyCorsHeaders(httplib::Response& response) {
    response.set_header("Access-Control-Allow-Origin", "*");
    response.set_header("Access-Control-Allow-Methods", "POST, DELETE, OPTIONS");
    response.set_header("Access-Control-Allow-Headers", "Authorization, Content-Type");
    response.set_header("Access-Control-Expose-Headers", "Location");
}

}  // namespace

WhepHandler::WhepHandler(PeerRegistry& registry, WhepCapabilityConfig authConfig,
                         std::vector<std::string> iceServers)
    : registry_(registry), authConfig_(std::move(authConfig)),
      iceServers_(std::move(iceServers)) {}

void WhepHandler::RegisterRoutes(httplib::Server& server) {
    server.Options("/whep", [](const httplib::Request&, httplib::Response& response) {
        ApplyCorsHeaders(response);
        response.status = 204;
    });

    server.Post("/whep", [this](const httplib::Request& request, httplib::Response& response) {
        ApplyCorsHeaders(response);
        if (!ValidateWhepCapability(authConfig_, ExtractBearerToken(request))) {
            response.status = 401;
            return;
        }

        std::string id;
        try {
            id = GenerateUnguessableId();
            auto session = registry_.Create(PeerKind::Local, id, iceServers_);
            if (!session) {
                response.status = 503;
                response.set_content("local session capacity reached", "text/plain");
                return;
            }

            const std::string answer = session->AnswerOffer(request.body);
            response.status = 201;
            response.set_header("Location", "/whep/" + id);
            response.set_content(answer, "application/sdp");
        } catch (const std::exception&) {
            if (!id.empty()) registry_.Remove(id);
            response.status = 500;
            response.set_content("failed to establish WHEP session", "text/plain");
        }
    });

    server.Delete(R"(/whep/([a-f0-9]{32}))",
                  [this](const httplib::Request& request, httplib::Response& response) {
                      ApplyCorsHeaders(response);
                      response.status = registry_.Remove(request.matches[1]) ? 204 : 404;
                  });
}
