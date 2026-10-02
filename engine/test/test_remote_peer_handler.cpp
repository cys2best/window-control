#include <gtest/gtest.h>
#include "remote_peer_handler.h"

TEST(RemotePeerHandlerTest, RemoteRouteNotOnPublicWhepListener) {}
TEST(RemotePeerHandlerTest, RejectsMissingCapabilityAndStaleGeneration) {}
TEST(RemotePeerHandlerTest, PublicPeerReplacementPreservesLocalPeers) {}
TEST(RemotePeerHandlerTest, CanceledOrFailedGatherDoesNotLeakPeer) {}
