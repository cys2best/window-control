#include "peer_registry.h"

PeerRegistry::PeerRegistry(int localCapacity, std::chrono::milliseconds handshakeTimeout)
    : localCapacity_(localCapacity), handshakeTimeout_(handshakeTimeout) {}

std::shared_ptr<PeerSession> PeerRegistry::Create(
    PeerKind kind, const std::string& id, const std::vector<std::string>& iceServers) {
    std::vector<std::shared_ptr<PeerSession>> victims;
    std::shared_ptr<PeerSession> session;

    {
        std::lock_guard<std::mutex> lock(mutex_);

        if (kind == PeerKind::Local) {
            size_t localCount = 0;
            for (const auto& [_, entry] : peers_) {
                if (entry.kind == PeerKind::Local) ++localCount;
            }
            if (localCount >= static_cast<size_t>(localCapacity_)) return nullptr;
        } else {
            // At most one public peer: drop the previous one before adding
            // its replacement, then close it outside the registry lock.
            for (auto it = peers_.begin(); it != peers_.end();) {
                if (it->second.kind == PeerKind::Public) {
                    victims.push_back(it->second.session);
                    it = peers_.erase(it);
                } else {
                    ++it;
                }
            }
        }

        session = std::make_shared<PeerSession>(id, iceServers);
        peers_[id] = Entry{session, kind, std::chrono::steady_clock::now()};
    }

    for (const auto& victim : victims) victim->Close();
    return session;
}

bool PeerRegistry::Remove(const std::string& id) {
    std::shared_ptr<PeerSession> victim;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        auto it = peers_.find(id);
        if (it == peers_.end()) return false;
        victim = it->second.session;
        peers_.erase(it);
    }
    victim->Close();
    return true;
}

bool PeerRegistry::MarkFailed(const std::string& id) {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = peers_.find(id);
    if (it == peers_.end()) return false;
    it->second.failed = true;
    return true;
}

std::shared_ptr<PeerSession> PeerRegistry::Find(const std::string& id) const {
    std::lock_guard<std::mutex> lock(mutex_);
    auto it = peers_.find(id);
    return it != peers_.end() ? it->second.session : nullptr;
}

std::vector<std::shared_ptr<PeerSession>> PeerRegistry::Snapshot() const {
    std::lock_guard<std::mutex> lock(mutex_);
    std::vector<std::shared_ptr<PeerSession>> result;
    result.reserve(peers_.size());
    for (const auto& [_, entry] : peers_) result.push_back(entry.session);
    return result;
}

void PeerRegistry::ReapDeadAndStalePeers() {
    std::vector<std::shared_ptr<PeerSession>> victims;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        auto now = std::chrono::steady_clock::now();
        for (auto it = peers_.begin(); it != peers_.end();) {
            auto state = it->second.session->State();
            bool dead = it->second.failed ||
                        state == rtc::PeerConnection::State::Failed ||
                        state == rtc::PeerConnection::State::Closed ||
                        state == rtc::PeerConnection::State::Disconnected;
            bool staleHandshake = state != rtc::PeerConnection::State::Connected &&
                                  (now - it->second.createdAt) > handshakeTimeout_;
            if (dead || staleHandshake) {
                victims.push_back(it->second.session);
                it = peers_.erase(it);
            } else {
                ++it;
            }
        }
    }

    for (const auto& victim : victims) victim->Close();
}

size_t PeerRegistry::LocalCount() const {
    std::lock_guard<std::mutex> lock(mutex_);
    size_t count = 0;
    for (const auto& [_, entry] : peers_) {
        if (entry.kind == PeerKind::Local) ++count;
    }
    return count;
}

bool PeerRegistry::HasPublicPeer() const {
    std::lock_guard<std::mutex> lock(mutex_);
    for (const auto& [_, entry] : peers_) {
        if (entry.kind == PeerKind::Public) return true;
    }
    return false;
}
