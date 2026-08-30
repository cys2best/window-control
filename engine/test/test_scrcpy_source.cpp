#include <gtest/gtest.h>
#include "scrcpy_source.h"
#include "fake_scrcpy_server.h"

#pragma comment(lib, "ws2_32.lib")

TEST(ScrcpySource, ConnectInitialSucceedsAndReportsDimensions) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    {
        ScrcpySource source(registry);
        source.ConnectInitial(fake.Port());

        auto status = source.Status();
        EXPECT_EQ(status.width, 100);
        EXPECT_EQ(status.height, 200);
        EXPECT_EQ(status.generation, 0u);
        EXPECT_EQ(status.state, SourceHealthState::Connected);
        EXPECT_NE(source.Control(), nullptr);
    }
}

TEST(ScrcpySource, RejectsStaleAndEqualReconnectGenerationsWithoutStateChange) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    {
        ScrcpySource source(registry);
        source.ConnectInitial(fake.Port());
        auto control = source.Control();
        auto before = source.Status();

        EXPECT_FALSE(source.Reconnect(fake.Port(), 0));
        EXPECT_FALSE(source.Reconnect(fake.Port(), before.generation));

        auto after = source.Status();
        EXPECT_EQ(after.generation, before.generation);
        EXPECT_EQ(after.width, before.width);
        EXPECT_EQ(after.height, before.height);
        EXPECT_EQ(source.Control(), control);
    }
}

TEST(ScrcpySource, ReconnectAdvancesGenerationAndPreservesPeerRegistry) {
    FakeScrcpyServer first(100, 200);
    FakeScrcpyServer second(300, 400);
    first.Serve();
    second.Serve();
    PeerRegistry registry;
    auto peer = registry.Create(PeerKind::Local, "existing-peer", {});
    ASSERT_NE(peer, nullptr);
    {
        ScrcpySource source(registry);
        source.ConnectInitial(first.Port());
        auto retiredControl = source.Control();
        ASSERT_TRUE(source.Reconnect(second.Port(), 2));

        auto status = source.Status();
        EXPECT_EQ(status.generation, 2u);
        EXPECT_EQ(status.width, 300);
        EXPECT_EQ(status.height, 400);
        EXPECT_EQ(status.state, SourceHealthState::Connected);
        EXPECT_EQ(registry.Find("existing-peer"), peer);
        EXPECT_FALSE(retiredControl->IsConnected());
    }
}

TEST(ScrcpySource, DestructionDisconnectsExternallyRetainedControl) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    std::shared_ptr<ScrcpyControlClient> retainedControl;
    {
        ScrcpySource source(registry);
        source.ConnectInitial(fake.Port());
        retainedControl = source.Control();
        ASSERT_TRUE(retainedControl->IsConnected());
    }

    EXPECT_FALSE(retainedControl->IsConnected());
}

TEST(ScrcpySource, FailedReconnectKeepsCommittedMetadataAndRetiresControl) {
    FakeScrcpyServer first(100, 200);
    FakeScrcpyServer broken(
        300, 400, FakeScrcpyServer::HandshakeBehavior::CloseBeforeMetadata);
    first.Serve();
    broken.Serve();
    PeerRegistry registry;
    {
        ScrcpySource source(registry);
        source.ConnectInitial(first.Port());
        auto retiredControl = source.Control();

        EXPECT_THROW(source.Reconnect(broken.Port(), 3), std::runtime_error);

        auto status = source.Status();
        EXPECT_EQ(status.generation, 0u);
        EXPECT_EQ(status.width, 100);
        EXPECT_EQ(status.height, 200);
        EXPECT_EQ(status.state, SourceHealthState::Disconnected);
        EXPECT_EQ(source.Control(), nullptr);
        EXPECT_FALSE(retiredControl->IsConnected());
    }
}

TEST(ScrcpySource, FailedInitialHandshakeLeavesDisconnectedWithoutControl) {
    FakeScrcpyServer broken(
        100, 200, FakeScrcpyServer::HandshakeBehavior::CloseBeforeMetadata);
    broken.Serve();
    PeerRegistry registry;
    ScrcpySource source(registry);

    EXPECT_THROW(source.ConnectInitial(broken.Port()), std::runtime_error);

    auto status = source.Status();
    EXPECT_EQ(status.state, SourceHealthState::Disconnected);
    EXPECT_EQ(status.generation, 0u);
    EXPECT_EQ(status.width, 0);
    EXPECT_EQ(status.height, 0);
    EXPECT_EQ(source.Control(), nullptr);
}

TEST(ScrcpySource, ReportsStalledAfterInactivityThreshold) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    {
        ScrcpySource source(registry, std::chrono::milliseconds(50));
        source.ConnectInitial(fake.Port());

        EXPECT_TRUE(PollUntil([&]() {
            return source.Status().state == SourceHealthState::Stalled;
        }));
    }
}

TEST(ScrcpySource, IncomingAccessUnitRefreshesStallHealth) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    {
        ScrcpySource source(registry, std::chrono::milliseconds(50));
        source.ConnectInitial(fake.Port());
        ASSERT_TRUE(PollUntil([&]() {
            return source.Status().state == SourceHealthState::Stalled;
        }));
        const std::vector<std::uint8_t> idr = {
            0x00, 0x00, 0x00, 0x01, 0x65, 0xAA, 0xBB};

        ASSERT_TRUE(fake.SendAccessUnit(idr));

        EXPECT_TRUE(PollUntil([&]() {
            return source.Status().state == SourceHealthState::Connected;
        }));
    }
}

TEST(ScrcpySource, DisconnectedControlCannotReportHealthyAfterFailureFlagReset) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    {
        ScrcpySource source(registry, std::chrono::seconds(30));
        source.ConnectInitial(fake.Port());
        auto control = source.Control();
        ASSERT_NE(control, nullptr);

        control->Disconnect();
        control->ResetSendFailureFlag();

        EXPECT_EQ(source.Status().state, SourceHealthState::Disconnected);
    }
}

TEST(ScrcpySource, UnexpectedVideoEofReportsDisconnectedImmediately) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    {
        ScrcpySource source(registry, std::chrono::seconds(30));
        source.ConnectInitial(fake.Port());
        fake.AbortVideo();

        EXPECT_TRUE(PollUntil([&]() {
            return source.Status().state == SourceHealthState::Disconnected;
        }));
    }
}

TEST(ScrcpySource, ControlSendFailureReportsDisconnectedImmediately) {
    FakeScrcpyServer fake;
    fake.Serve();
    PeerRegistry registry;
    {
        ScrcpySource source(registry, std::chrono::seconds(30));
        source.ConnectInitial(fake.Port());
        auto control = source.Control();
        fake.AbortControl();

        ASSERT_TRUE(PollUntil([&]() {
            source.RequestIdr();
            return source.Status().state == SourceHealthState::Disconnected;
        }));
        ASSERT_TRUE(control->LastSendFailed());

        control->ResetSendFailureFlag();
        EXPECT_FALSE(control->LastSendFailed());
        EXPECT_EQ(source.Status().state, SourceHealthState::Disconnected);
    }
}
