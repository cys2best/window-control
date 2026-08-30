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

    // Returns nullptr (caller returns 503) if kind==Local and the local
    // capacity is already full. A new Public peer always replaces and
    // closes any existing public peer first — at most one exists.
    std::shared_ptr<PeerSession> Create(
        PeerKind kind, const std::string& id,
        const std::vector<std::string>& iceServers);

    bool Remove(const std::string& id);
    // Records a send failure without closing the peer. The housekeeping
    // reaper performs the potentially blocking teardown off the media thread.
    bool MarkFailed(const std::string& id);
    std::shared_ptr<PeerSession> Find(const std::string& id) const;

    // Snapshot for the source's fan-out loop — never call SendVideoNalu
    // while holding the registry's internal lock (a slow/blocked peer
    // send must not stall peer add/remove for every other peer).
    std::vector<std::shared_ptr<PeerSession>> Snapshot() const;

    // Removes any peer whose State() is Failed/Closed/Disconnected, or
    // whose handshake has exceeded handshakeTimeout without reaching
    // Connected. Call periodically from a housekeeping loop.
    void ReapDeadAndStalePeers();

    size_t LocalCount() const;
    bool HasPublicPeer() const;

private:
    struct Entry {
        std::shared_ptr<PeerSession> session;
        PeerKind kind;
        std::chrono::steady_clock::time_point createdAt;
        bool failed = false;
    };

    mutable std::mutex mutex_;
    std::map<std::string, Entry> peers_;
    int localCapacity_;
    std::chrono::milliseconds handshakeTimeout_;
};
