#include <gtest/gtest.h>

#include "whep_handler.h"
#include "http_server.h"
#include "peer_registry.h"

#include <httplib.h>
#include <rtc/rtc.hpp>

#include <atomic>
#include <chrono>
#include <regex>
#include <stdexcept>
#include <string>
#include <thread>

namespace {

std::string GatheredOffer() {
    std::atomic<bool> gathered{false};
    std::string offer;
    {
        rtc::PeerConnection pc;
        pc.addTransceiver(rtc::Description::Media::Kind::Video,
                          rtc::Description::Direction::RecvOnly);
        pc.createDataChannel("input");

        pc.onGatheringStateChange([&](rtc::PeerConnection::GatheringState state) {
            if (state == rtc::PeerConnection::GatheringState::Complete) {
                gathered.store(true);
            }
        });
        pc.setLocalDescription();
        for (int i = 0; i < 200 && !gathered.load(); ++i) {
            std::this_thread::sleep_for(std::chrono::milliseconds(25));
        }
        if (!gathered.load()) {
            throw std::runtime_error("timed out gathering WHEP test offer");
        }

        const auto description = pc.localDescription();
        if (!description) {
            throw std::runtime_error("WHEP test offer has no local description");
        }
        offer = std::string(*description);
    }
    return offer;
}

}  // namespace

TEST(WhepHandler, PostWithoutAuthWhenDisabledReturnsAnswerAndDeleteCapability) {
    PeerRegistry registry;
    WhepHandler handler(registry, {"", "instance0"}, {});
    EngineHttpServer server("127.0.0.1");
    handler.RegisterRoutes(server.Server());
    server.Start();

    httplib::Client client("127.0.0.1", server.Port());
    auto post = client.Post("/whep", GatheredOffer(), "application/sdp");
    ASSERT_TRUE(post);
    EXPECT_EQ(post->status, 201);
    EXPECT_NE(post->body.find("v=0"), std::string::npos);
    const std::string location = post->get_header_value("Location");
    EXPECT_TRUE(std::regex_match(location, std::regex(R"(/whep/[a-f0-9]{32})")));
    EXPECT_EQ(post->get_header_value("Access-Control-Expose-Headers"), "Location");

    EXPECT_EQ(registry.LocalCount(), 1u);
    auto deleted = client.Delete(location);
    ASSERT_TRUE(deleted);
    EXPECT_EQ(deleted->status, 204);
    EXPECT_EQ(registry.LocalCount(), 0u);

    server.Stop();
}

TEST(WhepHandler, RejectsPostWithoutBearerWhenAuthIsEnabled) {
    PeerRegistry registry;
    WhepHandler handler(registry, {"secret", "instance0"}, {});
    EngineHttpServer server("127.0.0.1");
    handler.RegisterRoutes(server.Server());
    server.Start();

    httplib::Client client("127.0.0.1", server.Port());
    auto response = client.Post("/whep", GatheredOffer(), "application/sdp");
    ASSERT_TRUE(response);
    EXPECT_EQ(response->status, 401);
    EXPECT_EQ(registry.LocalCount(), 0u);

    server.Stop();
}

TEST(WhepHandler, ValidBearerPostCreatesDeleteCapabilityWithoutBearer) {
    PeerRegistry registry;
    WhepHandler handler(registry, {"secret", "instance0"}, {});
    EngineHttpServer server("127.0.0.1");
    handler.RegisterRoutes(server.Server());
    server.Start();

    httplib::Client client("127.0.0.1", server.Port());
    httplib::Headers headers{{
        "Authorization",
        "Bearer 4102444800.instance0.c3e2e0c219710589db54a974438715acbaf66c9ec3d6022261128c95f563dc2e",
    }};
    auto post = client.Post("/whep", headers, GatheredOffer(), "application/sdp");
    ASSERT_TRUE(post);
    ASSERT_EQ(post->status, 201);

    auto deleted = client.Delete(post->get_header_value("Location"));
    ASSERT_TRUE(deleted);
    EXPECT_EQ(deleted->status, 204);
    EXPECT_EQ(registry.LocalCount(), 0u);

    server.Stop();
}

TEST(WhepHandler, RejectsPostBeyondLocalCapacity) {
    PeerRegistry registry(/*localCapacity=*/1);
    WhepHandler handler(registry, {"", "instance0"}, {});
    EngineHttpServer server("127.0.0.1");
    handler.RegisterRoutes(server.Server());
    server.Start();

    httplib::Client client("127.0.0.1", server.Port());
    auto first = client.Post("/whep", GatheredOffer(), "application/sdp");
    ASSERT_TRUE(first);
    ASSERT_EQ(first->status, 201);

    auto second = client.Post("/whep", GatheredOffer(), "application/sdp");
    ASSERT_TRUE(second);
    EXPECT_EQ(second->status, 503);
    EXPECT_EQ(registry.LocalCount(), 1u);

    server.Stop();
}

TEST(WhepHandler, OptionsOnCreatedDeleteResourceReturnsCorsPolicy) {
    PeerRegistry registry;
    WhepHandler handler(registry, {"", "instance0"}, {});
    EngineHttpServer server("127.0.0.1");
    handler.RegisterRoutes(server.Server());
    server.Start();

    httplib::Client client("127.0.0.1", server.Port());
    auto post = client.Post("/whep", GatheredOffer(), "application/sdp");
    ASSERT_TRUE(post);
    ASSERT_EQ(post->status, 201);
    const std::string location = post->get_header_value("Location");
    ASSERT_FALSE(location.empty());

    auto response = client.Options(location);
    ASSERT_TRUE(response);
    EXPECT_EQ(response->status, 204);
    EXPECT_EQ(response->get_header_value("Access-Control-Allow-Headers"),
              "Authorization, Content-Type");
    EXPECT_EQ(response->get_header_value("Access-Control-Expose-Headers"), "Location");

    auto deleted = client.Delete(location);
    ASSERT_TRUE(deleted);
    EXPECT_EQ(deleted->status, 204);
    server.Stop();
}

TEST(WhepHandler, OptionsReturnsCorsPolicyForBearerSdpNegotiation) {
    PeerRegistry registry;
    WhepHandler handler(registry, {"", "instance0"}, {});
    EngineHttpServer server("127.0.0.1");
    handler.RegisterRoutes(server.Server());
    server.Start();

    httplib::Client client("127.0.0.1", server.Port());
    auto response = client.Options("/whep");
    ASSERT_TRUE(response);
    EXPECT_EQ(response->status, 204);
    EXPECT_EQ(response->get_header_value("Access-Control-Allow-Headers"),
              "Authorization, Content-Type");
    EXPECT_EQ(response->get_header_value("Access-Control-Expose-Headers"), "Location");

    server.Stop();
}
