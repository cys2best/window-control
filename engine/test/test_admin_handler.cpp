#include <gtest/gtest.h>
#include "admin_handler.h"
#include "scrcpy_source.h"
#include "peer_registry.h"
#include "http_server.h"
#include "fake_scrcpy_server.h"
#include "send_pacer.h"
#include <httplib.h>
#include <nlohmann/json.hpp>

using json = nlohmann::json;

TEST(AdminHandler, HealthReflectsSourceStatus) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());

    AdminHandler handler(source, registry);
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
    EXPECT_EQ(body["local_peers"], 0);
    EXPECT_EQ(body["public_peer"], false);

    server.Stop();
    fake.Stop();
}

TEST(AdminHandler, HealthReflectsPeerCountsAndRemoval) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());
    AdminHandler handler(source, registry);
    EngineHttpServer server("127.0.0.1");
    handler.RegisterRoutes(server.Server());
    server.Start();

    ASSERT_NE(registry.Create(PeerKind::Local, "local-1", {}), nullptr);
    ASSERT_NE(registry.Create(PeerKind::Local, "local-2", {}), nullptr);
    ASSERT_NE(registry.Create(PeerKind::Public, "public-1", {}), nullptr);

    httplib::Client client("127.0.0.1", server.Port());
    auto activeRes = client.Get("/admin/health");
    ASSERT_TRUE(activeRes);
    auto active = json::parse(activeRes->body);
    EXPECT_EQ(active["local_peers"], 2);
    EXPECT_EQ(active["public_peer"], true);

    EXPECT_TRUE(registry.Remove("local-1"));
    EXPECT_TRUE(registry.Remove("local-2"));
    EXPECT_TRUE(registry.Remove("public-1"));
    auto removedRes = client.Get("/admin/health");
    ASSERT_TRUE(removedRes);
    auto removed = json::parse(removedRes->body);
    EXPECT_EQ(removed["local_peers"], 0);
    EXPECT_EQ(removed["public_peer"], false);

    server.Stop();
    fake.Stop();
}

TEST(AdminHandler, ReconnectAcceptsNewerGenerationAndRejectsStale) {
    FakeScrcpyServer fake1;
    fake1.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake1.Port());

    AdminHandler handler(source, registry);
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

TEST(AdminHandler, AcceptedReconnectAdoptsTheNewEncoderBitrate) {
    FakeScrcpyServer fake1;
    fake1.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake1.Port());
    video_target::SetBitsPerSecond(4'000'000.0);

    AdminHandler handler(source, registry);
    EngineHttpServer server("127.0.0.1");
    handler.RegisterRoutes(server.Server());
    server.Start();

    FakeScrcpyServer fake2;
    fake2.Serve();
    httplib::Client client("127.0.0.1", server.Port());

    // A rejected (stale) reconnect must not change what sends are paced
    // against: the old source is still the one streaming.
    json staleBody = {{"scrcpy_port", fake2.Port()}, {"generation", 0}, {"video_bit_rate", 800000}};
    auto staleRes = client.Post("/admin/reconnect", staleBody.dump(), "application/json");
    ASSERT_TRUE(staleRes);
    EXPECT_EQ(staleRes->status, 409);
    EXPECT_DOUBLE_EQ(video_target::BitsPerSecond(), 4'000'000.0);

    json freshBody = {{"scrcpy_port", fake2.Port()}, {"generation", 1}, {"video_bit_rate", 800000}};
    auto freshRes = client.Post("/admin/reconnect", freshBody.dump(), "application/json");
    ASSERT_TRUE(freshRes);
    EXPECT_EQ(freshRes->status, 200);
    EXPECT_DOUBLE_EQ(video_target::BitsPerSecond(), 800'000.0);

    server.Stop();
    fake1.Stop();
    fake2.Stop();
    video_target::SetBitsPerSecond(0.0);
}

TEST(AdminHandler, ReconnectWithoutAUsableBitrateLeavesPacingAsItWas) {
    FakeScrcpyServer fake1;
    fake1.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake1.Port());
    video_target::SetBitsPerSecond(4'000'000.0);

    AdminHandler handler(source, registry);
    EngineHttpServer server("127.0.0.1");
    handler.RegisterRoutes(server.Server());
    server.Start();
    httplib::Client client("127.0.0.1", server.Port());

    // An older host sends no bitrate at all.
    FakeScrcpyServer fake2;
    fake2.Serve();
    json withoutBitrate = {{"scrcpy_port", fake2.Port()}, {"generation", 1}};
    auto firstRes = client.Post("/admin/reconnect", withoutBitrate.dump(), "application/json");
    ASSERT_TRUE(firstRes);
    EXPECT_EQ(firstRes->status, 200);
    EXPECT_DOUBLE_EQ(video_target::BitsPerSecond(), 4'000'000.0);

    // A bitrate of the wrong type is ignored rather than rejected: the
    // reconnect itself is still valid.
    FakeScrcpyServer fake3;
    fake3.Serve();
    json wrongType = {{"scrcpy_port", fake3.Port()}, {"generation", 2}, {"video_bit_rate", "fast"}};
    auto secondRes = client.Post("/admin/reconnect", wrongType.dump(), "application/json");
    ASSERT_TRUE(secondRes);
    EXPECT_EQ(secondRes->status, 200);
    EXPECT_DOUBLE_EQ(video_target::BitsPerSecond(), 4'000'000.0);

    server.Stop();
    fake1.Stop();
    fake2.Stop();
    fake3.Stop();
    video_target::SetBitsPerSecond(0.0);
}

TEST(AdminHandler, KeyframeReturns204) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());

    AdminHandler handler(source, registry);
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

TEST(AdminHandler, ReconnectRejectsWrongFieldTypesWith400) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(fake.Port());

    AdminHandler handler(source, registry);
    EngineHttpServer server("127.0.0.1");
    handler.RegisterRoutes(server.Server());
    server.Start();

    httplib::Client client("127.0.0.1", server.Port());
    json body = {{"scrcpy_port", "not-a-port"}, {"generation", 1}};
    auto res = client.Post("/admin/reconnect", body.dump(), "application/json");

    ASSERT_TRUE(res);
    EXPECT_EQ(res->status, 400);

    server.Stop();
    fake.Stop();
}

TEST(AdminHandler, ReconnectFailureReturnsStructured502) {
    FakeScrcpyServer initial;
    initial.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);
    source.ConnectInitial(initial.Port());

    AdminHandler handler(source, registry);
    EngineHttpServer server("127.0.0.1");
    handler.RegisterRoutes(server.Server());
    server.Start();

    FakeScrcpyServer failing(
        100, 200, FakeScrcpyServer::HandshakeBehavior::CloseBeforeMetadata);
    failing.Serve();
    httplib::Client client("127.0.0.1", server.Port());
    json requestBody = {{"scrcpy_port", failing.Port()}, {"generation", 1}};
    auto res = client.Post(
        "/admin/reconnect", requestBody.dump(), "application/json");

    ASSERT_TRUE(res);
    EXPECT_EQ(res->status, 502);
    auto responseBody = json::parse(res->body);
    EXPECT_EQ(responseBody["accepted"], false);
    EXPECT_TRUE(responseBody["error"].is_string());
    EXPECT_FALSE(responseBody["error"].get<std::string>().empty());
    EXPECT_EQ(responseBody["current_generation"], 0);

    server.Stop();
    initial.Stop();
    failing.Stop();
}
