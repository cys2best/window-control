#pragma once
#include "peer_session.h"
#include <chrono>
#include <map>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

enum class PeerKind { Local, Public };

class PeerRegistry {
public:
    explicit PeerRegistry(int localCapacity = 4,
                           std::chrono::milliseconds handshakeTimeout =
                               std::chrono::milliseconds(15000));

    std::shared_ptr<PeerSession> Create(
        PeerKind kind, const std::string& id,
        const std::vector<std::string>& iceServers);

    bool Remove(const std::string& id);
    std::shared_ptr<PeerSession> Find(const std::string& id) const;
    std::vector<std::shared_ptr<PeerSession>> Snapshot() const;
    void ReapDeadAndStalePeers();

    size_t LocalCount() const;
    bool HasPublicPeer() const;

private:
    struct Entry {
        std::shared_ptr<PeerSession> session;
        PeerKind kind;
        std::chrono::steady_clock::time_point createdAt;
    };

    mutable std::mutex mutex_;
    std::map<std::string, Entry> peers_;
    int localCapacity_;
    std::chrono::milliseconds handshakeTimeout_;
};
