#include <gtest/gtest.h>
#include "admin_handler.h"
#include "scrcpy_source.h"
#include "peer_registry.h"
#include "http_server.h"
#include "fake_scrcpy_server.h"
#include <httplib.h>
#include <nlohmann/json.hpp>

using json = nlohmann::json;

TEST(AdminHandler, HealthReflectsSourceStatus) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());

    AdminHandler handler(source);
    EngineHttpServer server("127.0.0.1");
    handler.RegisterRoutes(server.Server());
    server.Start();

    httplib::Client client("127.0.0.1", server.Port());
    auto res = client.Get("/admin/health");
    ASSERT_TRUE(res);
    EXPECT_EQ(res->status, 200);
    auto body = json::parse(res->body);
    EXPECT_EQ(body["state"], "connected");
    EXPECT_EQ(body["width"], 100);
    EXPECT_EQ(body["height"], 200);

    server.Stop();
    fake.Stop();
}

TEST(AdminHandler, ReconnectAcceptsNewerGenerationAndRejectsStale) {
    FakeScrcpyServer fake1;
    fake1.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake1.Port());

    AdminHandler handler(source);
    EngineHttpServer server("127.0.0.1");
    handler.RegisterRoutes(server.Server());
    server.Start();

    FakeScrcpyServer fake2;
    fake2.Serve();
    httplib::Client client("127.0.0.1", server.Port());

    json staleBody = {{"scrcpy_port", fake2.Port()}, {"generation", 0}};
    auto staleRes = client.Post("/admin/reconnect", staleBody.dump(), "application/json");
    ASSERT_TRUE(staleRes);
    EXPECT_EQ(staleRes->status, 409);

    json freshBody = {{"scrcpy_port", fake2.Port()}, {"generation", 1}};
    auto freshRes = client.Post("/admin/reconnect", freshBody.dump(), "application/json");
    ASSERT_TRUE(freshRes);
    EXPECT_EQ(freshRes->status, 200);
    EXPECT_EQ(source.Status().generation, 1u);

    server.Stop();
    fake1.Stop();
    fake2.Stop();
}

TEST(AdminHandler, KeyframeReturns204) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());

    AdminHandler handler(source);
    EngineHttpServer server("127.0.0.1");
    handler.RegisterRoutes(server.Server());
    server.Start();

    httplib::Client client("127.0.0.1", server.Port());
    auto res = client.Post("/admin/keyframe", "", "application/json");
    ASSERT_TRUE(res);
    EXPECT_EQ(res->status, 204);

    server.Stop();
    fake.Stop();
}
