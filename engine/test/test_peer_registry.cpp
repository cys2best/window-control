#include <gtest/gtest.h>
#include "peer_registry.h"
#include <thread>
#include <chrono>

TEST(PeerRegistry, EnforcesLocalCapacityLimit) {
    PeerRegistry registry(/*localCapacity=*/2);
    auto p1 = registry.Create(PeerKind::Local, "l1", {});
    auto p2 = registry.Create(PeerKind::Local, "l2", {});
    auto p3 = registry.Create(PeerKind::Local, "l3", {});

    EXPECT_NE(p1, nullptr);
    EXPECT_NE(p2, nullptr);
    EXPECT_EQ(p3, nullptr);
    EXPECT_EQ(registry.LocalCount(), 2u);
}

TEST(PeerRegistry, NewPublicPeerReplacesPrevious) {
    PeerRegistry registry;
    auto first = registry.Create(PeerKind::Public, "pub1", {});
    ASSERT_NE(first, nullptr);
    EXPECT_TRUE(registry.HasPublicPeer());

    auto second = registry.Create(PeerKind::Public, "pub2", {});
    ASSERT_NE(second, nullptr);
    EXPECT_EQ(registry.Find("pub1"), nullptr);
    EXPECT_NE(registry.Find("pub2"), nullptr);
}

TEST(PeerRegistry, RemoveDropsPeerAndFreesCapacity) {
    PeerRegistry registry(/*localCapacity=*/1);
    auto p1 = registry.Create(PeerKind::Local, "l1", {});
    ASSERT_NE(p1, nullptr);
    EXPECT_TRUE(registry.Remove("l1"));
    EXPECT_EQ(registry.LocalCount(), 0u);

    auto p2 = registry.Create(PeerKind::Local, "l2", {});
    EXPECT_NE(p2, nullptr);
}

TEST(PeerRegistry, ReapDeadAndStalePeersRemovesTimedOutHandshake) {
    PeerRegistry registry(/*localCapacity=*/4, /*handshakeTimeout=*/std::chrono::milliseconds(50));
    auto p1 = registry.Create(PeerKind::Local, "l1", {});
    ASSERT_NE(p1, nullptr);

    std::this_thread::sleep_for(std::chrono::milliseconds(100));
    registry.ReapDeadAndStalePeers();

    EXPECT_EQ(registry.Find("l1"), nullptr);
    EXPECT_EQ(registry.LocalCount(), 0u);
}

TEST(PeerRegistry, SnapshotReflectsCurrentPeers) {
    PeerRegistry registry;
    registry.Create(PeerKind::Local, "l1", {});
    registry.Create(PeerKind::Public, "pub1", {});

    auto snap = registry.Snapshot();
    EXPECT_EQ(snap.size(), 2u);
}

TEST(PeerRegistry, MarkFailedDefersRemovalUntilReap) {
    PeerRegistry registry;
    auto peer = registry.Create(PeerKind::Local, "failed-send", {});
    ASSERT_NE(peer, nullptr);

    EXPECT_TRUE(registry.MarkFailed("failed-send"));
    EXPECT_EQ(registry.Find("failed-send"), peer);

    registry.ReapDeadAndStalePeers();

    EXPECT_EQ(registry.Find("failed-send"), nullptr);
}
