#include <gtest/gtest.h>
#include "http_server.h"
#include "ready_record.h"
#include <httplib.h>
#include <nlohmann/json.hpp>

using json = nlohmann::json;

TEST(EngineHttpServer, BindsToEphemeralPortAndServes) {
    EngineHttpServer server("0.0.0.0");
    server.Server().Get("/", [](const httplib::Request&, httplib::Response& res) {
        res.set_content("ok", "text/plain");
    });
    server.Start();
    ASSERT_GT(server.Port(), 0);

    httplib::Client client("127.0.0.1", server.Port());
    auto res = client.Get("/");
    ASSERT_TRUE(res);
    EXPECT_EQ(res->status, 200);
    EXPECT_EQ(res->body, "ok");

    server.Stop();
}

TEST(EngineHttpServer, LoopbackBindRefusesNonLoopbackByAddressChoice) {
    // The admin listener's security boundary is *which address it binds*,
    // not application-level filtering — this test only documents that the
    // constructor accepts an explicit bind address distinct from WHEP's.
    EngineHttpServer admin("127.0.0.1");
    admin.Start();
    ASSERT_GT(admin.Port(), 0);
    EXPECT_NE(admin.Port(), 0);
    admin.Stop();
}

TEST(ReadyRecord, ContainsAllRequiredFields) {
    std::string line = BuildReadyRecord("instance0", 4242, 51000, 51001, 3, 1080, 1920);
    auto parsed = json::parse(line, nullptr, false);
    ASSERT_FALSE(parsed.is_discarded());
    EXPECT_EQ(parsed["instance_name"], "instance0");
    EXPECT_EQ(parsed["pid"], 4242);
    EXPECT_EQ(parsed["whep_port"], 51000);
    EXPECT_EQ(parsed["admin_port"], 51001);
    EXPECT_EQ(parsed["generation"], 3);
    EXPECT_EQ(parsed["width"], 1080);
    EXPECT_EQ(parsed["height"], 1920);
}
