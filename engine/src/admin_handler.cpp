#include "admin_handler.h"
#include <iostream>
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
        int port;
        std::uint64_t generation;
        try {
            port = body["scrcpy_port"].get<int>();
            generation = body["generation"].get<std::uint64_t>();
        } catch (const json::exception& e) {
            std::cerr << "[admin] invalid reconnect request: " << e.what() << std::endl;
            res.status = 400;
            res.set_content(
                json{{"accepted", false}, {"error", "invalid reconnect field types"}}.dump(),
                "application/json");
            return;
        }

        bool accepted;
        try {
            accepted = source_.Reconnect(port, generation);
        } catch (const std::exception& e) {
            auto currentGeneration = source_.Status().generation;
            std::cerr << "[admin] reconnect failed: " << e.what() << std::endl;
            res.status = 502;
            res.set_content(
                json{
                    {"accepted", false},
                    {"error", e.what()},
                    {"current_generation", currentGeneration},
                }.dump(),
                "application/json");
            return;
        }
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
