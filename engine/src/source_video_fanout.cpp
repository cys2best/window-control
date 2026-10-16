#include "source_video_fanout.h"

#include <exception>
#include <iostream>
#include <utility>

std::optional<std::vector<std::uint8_t>>
H264SourceAccessUnitPreparer::ObserveAndPrepare(
    const std::uint8_t* data,
    std::size_t size) {
    return cache_.ObserveAndPrepare(data, size);
}

void H264SourceAccessUnitPreparer::Reset() {
    cache_.Reset();
}

SourceVideoFanout::SourceVideoFanout(
    SourceAccessUnitPreparer& preparer,
    SnapshotProvider snapshotProvider)
    : preparer_(preparer),
      snapshotProvider_(std::move(snapshotProvider)) {}

void SourceVideoFanout::BeginGeneration() {
    preparer_.Reset();
}

void SourceVideoFanout::SendAccessUnit(
    const std::uint8_t* data,
    std::size_t size) {
    auto prepared = preparer_.ObserveAndPrepare(data, size);
    const std::uint8_t* sendData = data;
    std::size_t sendSize = size;
    if (prepared.has_value()) {
        sendData = prepared->data();
        sendSize = prepared->size();
    }

    auto peers = snapshotProvider_();
    std::vector<const SourceVideoPeerTarget*> failedPeers;
    for (const auto& peer : peers) {
        try {
            peer.send(sendData, sendSize);
        } catch (const std::exception& error) {
            std::cerr << "[peer] video send failed id=" << peer.id
                      << ": " << error.what() << std::endl;
            failedPeers.push_back(&peer);
        } catch (...) {
            std::cerr << "[peer] video send failed id=" << peer.id
                      << ": unknown exception" << std::endl;
            failedPeers.push_back(&peer);
        }
    }

    for (const auto* peer : failedPeers) MarkFailedPeer(*peer);
}

void SourceVideoFanout::MarkFailedPeer(const SourceVideoPeerTarget& peer) {
    if (!peer.markFailed) return;
    try {
        peer.markFailed();
    } catch (const std::exception& error) {
        std::cerr << "[peer] failed to mark video-send peer id=" << peer.id
                  << ": " << error.what() << std::endl;
    } catch (...) {
        std::cerr << "[peer] failed to mark video-send peer id=" << peer.id
                  << ": unknown exception" << std::endl;
    }
}
