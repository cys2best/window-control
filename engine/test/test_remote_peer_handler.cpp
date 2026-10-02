#include <gtest/gtest.h>
#include "remote_peer_handler.h"
#include "fake_scrcpy_server.h"
#include "http_server.h"
#include "whep_handler.h"
#include <nlohmann/json.hpp>
#include <atomic>
#include <chrono>
#include <regex>
#include <thread>

using json = nlohmann::json;
namespace {
constexpr const char* kToken = "Bearer 4102444800.instance0.c3e2e0c219710589db54a974438715acbaf66c9ec3d6022261128c95f563dc2e";
const httplib::Headers kAuth{{"Authorization", kToken}};

std::string GatheredOffer() {
    rtc::Configuration config;
    config.disableAutoNegotiation = true;
    rtc::PeerConnection viewer(config);
    rtc::Description::Video video("0", rtc::Description::Direction::RecvOnly);
    video.addH264Codec(103);
    auto track = viewer.addTrack(video);
    auto input = viewer.createDataChannel("input");
    std::atomic<bool> gathered{false};
    viewer.onGatheringStateChange([&](auto state) {
        if (state == rtc::PeerConnection::GatheringState::Complete) gathered = true;
    });
    viewer.setLocalDescription();
    for (int i = 0; i < 200 && !gathered; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(25));
    if (!gathered || !viewer.localDescription()) throw std::runtime_error("test offer gathering failed");
    auto result = std::string(*viewer.localDescription());
    viewer.resetCallbacks();
    viewer.close();
    return result;
}

struct RemotePeerHandlerTest : testing::Test {
    FakeScrcpyServer fake;
    PeerRegistry registry;
    ScrcpySource source{registry};
    InputRouter input{source};
    RemotePeerHandler handler{registry, source, input, {"secret", "instance0"}};
    EngineHttpServer admin{"127.0.0.1"};
    void SetUp() override {
        fake.Serve();
        source.ConnectInitial(fake.Port());
        handler.RegisterRoutes(admin.Server());
        admin.Start();
    }
    void TearDown() override {
        admin.Stop();
        input.ShutdownPeers(registry);
        fake.Stop();
    }
    json Body(const std::string& offer = "invalid SDP", std::uint64_t generation = 0) {
        return {{"session_id", "session-1"}, {"generation", generation}, {"offer", offer},
                {"ice_servers", json::array()}, {"timeout_ms", 5000}};
    }
};
}

TEST_F(RemotePeerHandlerTest, RemoteRouteNotOnPublicWhepListener) {
    WhepHandler whep(registry, {"", "instance0"}, {}, input);
    EngineHttpServer publicServer("0.0.0.0");
    whep.RegisterRoutes(publicServer.Server());
    publicServer.Start();
    httplib::Client client("127.0.0.1", publicServer.Port());
    auto result = client.Post("/admin/remote-peers", kAuth, Body().dump(), "application/json");
    ASSERT_TRUE(result);
    EXPECT_EQ(result->status, 404);
    auto deleted = client.Delete("/admin/remote-peers/0123456789abcdef0123456789abcdef", kAuth);
    ASSERT_TRUE(deleted);
    EXPECT_EQ(deleted->status, 404);
    EXPECT_TRUE(registry.Snapshot().empty());
    publicServer.Stop();
}

TEST_F(RemotePeerHandlerTest, RejectsMissingCapabilityAndStaleGeneration) {
    httplib::Client client("127.0.0.1", admin.Port());
    auto missing = client.Post("/admin/remote-peers", Body().dump(), "application/json");
    ASSERT_TRUE(missing);
    EXPECT_EQ(missing->status, 401);
    auto stale = client.Post("/admin/remote-peers", kAuth, Body("invalid SDP", 1).dump(), "application/json");
    ASSERT_TRUE(stale);
    EXPECT_EQ(stale->status, 409);
    auto deleted = client.Delete("/admin/remote-peers/0123456789abcdef0123456789abcdef");
    ASSERT_TRUE(deleted);
    EXPECT_EQ(deleted->status, 401);
    EXPECT_TRUE(registry.Snapshot().empty());
}

TEST_F(RemotePeerHandlerTest, PublicPeerReplacementPreservesLocalPeers) {
    auto local = registry.Create(PeerKind::Local, "0123456789abcdef0123456789abcdef", {});
    ASSERT_TRUE(local);
    httplib::Client client("127.0.0.1", admin.Port());
    auto first = client.Post("/admin/remote-peers", kAuth, Body(GatheredOffer()).dump(), "application/json");
    ASSERT_TRUE(first);
    ASSERT_EQ(first->status, 201);
    auto firstBody = json::parse(first->body);
    auto firstId = firstBody["peer_id"].get<std::string>();
    EXPECT_TRUE(std::regex_match(firstId, std::regex("[a-f0-9]{32}")));
    EXPECT_EQ(firstBody["generation"], 0);
    EXPECT_NE(firstBody["answer"].get<std::string>().find("a=candidate:"), std::string::npos);
    auto second = client.Post("/admin/remote-peers", kAuth, Body(GatheredOffer()).dump(), "application/json");
    ASSERT_TRUE(second);
    ASSERT_EQ(second->status, 201);
    auto secondId = json::parse(second->body)["peer_id"].get<std::string>();
    EXPECT_EQ(registry.Find(firstId), nullptr);
    EXPECT_TRUE(registry.Find(secondId));
    EXPECT_EQ(registry.Find(local->Id()), local);
    EXPECT_EQ(registry.LocalCount(), 1u);
    auto localDelete = client.Delete("/admin/remote-peers/" + local->Id(), kAuth);
    ASSERT_TRUE(localDelete);
    EXPECT_EQ(localDelete->status, 404);
    auto deleted = client.Delete("/admin/remote-peers/" + secondId, kAuth);
    ASSERT_TRUE(deleted);
    EXPECT_EQ(deleted->status, 204);
    EXPECT_FALSE(registry.HasPublicPeer());
    EXPECT_EQ(registry.Find(local->Id()), local);
}

TEST_F(RemotePeerHandlerTest, CanceledOrFailedGatherDoesNotLeakPeer) {
    auto existing = registry.Create(PeerKind::Public, "old-public", {});
    auto local = registry.Create(PeerKind::Local, "local", {});
    httplib::Client client("127.0.0.1", admin.Port());
    auto failed = client.Post("/admin/remote-peers", kAuth, Body().dump(), "application/json");
    ASSERT_TRUE(failed);
    EXPECT_EQ(failed->status, 502);
    httplib::Request canceled;
    canceled.method = "POST";
    canceled.path = "/admin/remote-peers";
    canceled.remote_addr = "127.0.0.1";
    canceled.headers = kAuth;
    canceled.body = Body(GatheredOffer()).dump();
    canceled.is_connection_closed = [] { return true; };
    httplib::Response response;
    handler(canceled, response);
    EXPECT_EQ(response.status, 408);
    int cancellationChecks = 0;
    canceled.is_connection_closed = [&] { return ++cancellationChecks >= 4; };
    httplib::Response duringGather;
    handler(canceled, duringGather);
    EXPECT_EQ(duringGather.status, 408);
    EXPECT_GE(cancellationChecks, 4);
    EXPECT_EQ(registry.Find("old-public"), existing);
    EXPECT_EQ(registry.Find("local"), local);
    EXPECT_EQ(registry.Snapshot().size(), 2u);
}

TEST_F(RemotePeerHandlerTest, RemoteInputDataChannelUsesExistingRouter) {
    std::atomic<bool> gathered{false};
    std::atomic<bool> open{false};
    std::atomic<bool> echoed{false};
    rtc::Configuration config;
    config.disableAutoNegotiation = true;
    rtc::PeerConnection viewer(config);
    rtc::Description::Video video("0", rtc::Description::Direction::RecvOnly);
    video.addH264Codec(103);
    auto track = viewer.addTrack(video);
    auto channel = viewer.createDataChannel("input");
    viewer.onGatheringStateChange([&](auto state) {
        if (state == rtc::PeerConnection::GatheringState::Complete) gathered = true;
    });
    channel->onOpen([&] { open = true; });
    channel->onMessage([&](rtc::message_variant message) {
        if (std::holds_alternative<std::string>(message) &&
            std::get<std::string>(message) == R"({"type":"echo","t":123})") echoed = true;
    });
    viewer.setLocalDescription();
    for (int i = 0; i < 200 && !gathered; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(25));
    ASSERT_TRUE(gathered);
    ASSERT_TRUE(viewer.localDescription());
    httplib::Client client("127.0.0.1", admin.Port());
    auto posted = client.Post("/admin/remote-peers", kAuth,
                              Body(std::string(*viewer.localDescription())).dump(), "application/json");
    ASSERT_TRUE(posted);
    ASSERT_EQ(posted->status, 201);
    auto response = json::parse(posted->body);
    EXPECT_NO_THROW(viewer.setRemoteDescription(rtc::Description(response["answer"].get<std::string>(), "answer")));
    for (int i = 0; i < 200 && !open; ++i) std::this_thread::sleep_for(std::chrono::milliseconds(25));
    ASSERT_TRUE(open);
    channel->send(std::string(R"({"type":"echo","t":123})"));
    ASSERT_TRUE(PollUntil([&] { return echoed.load(); }));
    auto deleted = client.Delete("/admin/remote-peers/" + response["peer_id"].get<std::string>(), kAuth);
    ASSERT_TRUE(deleted);
    EXPECT_EQ(deleted->status, 204);
    viewer.resetCallbacks();
    channel->resetCallbacks();
    viewer.close();
}

TEST_F(RemotePeerHandlerTest, DeletionAndAdoptionRejectRetiredGeneration) {
    httplib::Client client("127.0.0.1", admin.Port());
    auto posted = client.Post("/admin/remote-peers", kAuth, Body(GatheredOffer()).dump(), "application/json");
    ASSERT_TRUE(posted);
    ASSERT_EQ(posted->status, 201);
    auto peerId = json::parse(posted->body)["peer_id"].get<std::string>();
    FakeScrcpyServer replacement;
    replacement.Serve();
    ASSERT_TRUE(source.Reconnect(replacement.Port(), 1));
    auto deleted = client.Delete("/admin/remote-peers/" + peerId, kAuth);
    ASSERT_TRUE(deleted);
    EXPECT_EQ(deleted->status, 409);
    bool ran = false;
    EXPECT_FALSE(source.WithGeneration(0, [&] { ran = true; }));
    EXPECT_FALSE(ran);
    EXPECT_TRUE(registry.Find(peerId));
    replacement.Stop();
}

TEST_F(RemotePeerHandlerTest, SourceReconnectDuringNegotiationCannotAdoptStalePeer) {
    auto local = registry.Create(PeerKind::Local, "local", {});
    auto oldPublic = registry.Create(PeerKind::Public, "old-public", {});
    FakeScrcpyServer replacement;
    replacement.Serve();
    httplib::Request request;
    request.remote_addr = "127.0.0.1";
    request.method = "POST";
    request.path = "/admin/remote-peers";
    request.headers = kAuth;
    request.body = Body(GatheredOffer()).dump();
    bool reconnected = false;
    // A real reconnect after the initial validation simulates a source switch
    // while negotiation is outstanding. The final generation check must reject
    // this fully gathered answer without replacing either existing peer.
    request.is_connection_closed = [&] {
        if (!reconnected) {
            reconnected = source.Reconnect(replacement.Port(), 1);
        }
        return false;
    };
    httplib::Response response;
    handler(request, response);
    EXPECT_TRUE(reconnected);
    EXPECT_EQ(response.status, 409);
    EXPECT_EQ(registry.Find("local"), local);
    EXPECT_EQ(registry.Find("old-public"), oldPublic);
    EXPECT_EQ(registry.Snapshot().size(), 2u);
    EXPECT_EQ(source.Status().generation, 1u);
    replacement.Stop();
}

TEST_F(RemotePeerHandlerTest, RejectsInvalidRequestBoundsAndNonLoopbackCaller) {
    httplib::Client client("127.0.0.1", admin.Port());
    for (auto timeout : {0, -1, 30001}) {
        auto body = Body(); body["timeout_ms"] = timeout;
        auto result = client.Post("/admin/remote-peers", kAuth, body.dump(), "application/json");
        ASSERT_TRUE(result); EXPECT_EQ(result->status, 400);
    }
    auto withinDeadline = Body(); withinDeadline["timeout_ms"] = 30000;
    auto acceptedBudget = client.Post("/admin/remote-peers", kAuth, withinDeadline.dump(), "application/json");
    ASSERT_TRUE(acceptedBudget);
    EXPECT_EQ(acceptedBudget->status, 502);  // budget accepted; SDP is intentionally invalid
    auto oversizedOffer = Body(std::string(128 * 1024 + 1, 's'));
    auto tooLarge = client.Post("/admin/remote-peers", kAuth, oversizedOffer.dump(), "application/json");
    ASSERT_TRUE(tooLarge);
    EXPECT_EQ(tooLarge->status, 400);
    auto body = Body(); body["generation"] = -1;
    auto result = client.Post("/admin/remote-peers", kAuth, body.dump(), "application/json");
    ASSERT_TRUE(result); EXPECT_EQ(result->status, 400);
    httplib::Request external;
    external.remote_addr = "192.0.2.1";
    external.headers = kAuth;
    external.method = "POST";
    external.body = Body().dump();
    httplib::Response response;
    handler(external, response);
    EXPECT_EQ(response.status, 403);
    EXPECT_TRUE(registry.Snapshot().empty());
}
