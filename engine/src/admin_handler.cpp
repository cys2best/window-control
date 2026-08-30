#include "admin_handler.h"
#include <nlohmann/json.hpp>

using json = nlohmann::json;

namespace {
std::string StateToString(SourceHealthState state) {
    switch (state) {
        case SourceHealthState::Connected: return "connected";
        case SourceHealthState::Stalled: return "stalled";
        default: return "disconnected";
    }
}
}

AdminHandler::AdminHandler(ScrcpySource& source) : source_(source) {}

void AdminHandler::RegisterRoutes(httplib::Server& server) {
    server.Get("/admin/health", [this](const httplib::Request&, httplib::Response& res) {
        auto status = source_.Status();
        json body = {
            {"state", StateToString(status.state)},
            {"generation", status.generation},
            {"width", status.width},
            {"height", status.height},
        };
        res.set_content(body.dump(), "application/json");
    });

    server.Post("/admin/reconnect", [this](const httplib::Request& req, httplib::Response& res) {
        auto body = json::parse(req.body, nullptr, false);
        if (body.is_discarded() || !body.contains("scrcpy_port") || !body.contains("generation")) {
            res.status = 400;
            return;
        }
        int port = body["scrcpy_port"].get<int>();
        std::uint64_t generation = body["generation"].get<std::uint64_t>();

        bool accepted = source_.Reconnect(port, generation);
        res.status = accepted ? 200 : 409;
        json responseBody = accepted
            ? json{{"accepted", true}, {"generation", generation}}
            : json{{"accepted", false}, {"current_generation", source_.Status().generation}};
        res.set_content(responseBody.dump(), "application/json");
    });

    server.Post("/admin/keyframe", [this](const httplib::Request&, httplib::Response& res) {
        source_.RequestIdr();
        res.status = 204;
    });
}
