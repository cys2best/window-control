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
    SnapshotProvider snapshotProvider,
    RemovePeer removePeer)
    : preparer_(preparer),
      snapshotProvider_(std::move(snapshotProvider)),
      removePeer_(std::move(removePeer)) {}

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
    for (const auto& peer : peers) {
        try {
            peer.send(sendData, sendSize);
        } catch (const std::exception& error) {
            std::cerr << "[peer] video send failed id=" << peer.id
                      << ": " << error.what() << std::endl;
            RemoveFailedPeer(peer.id);
        } catch (...) {
            std::cerr << "[peer] video send failed id=" << peer.id
                      << ": unknown exception" << std::endl;
            RemoveFailedPeer(peer.id);
        }
    }
}

void SourceVideoFanout::RemoveFailedPeer(const std::string& id) {
    try {
        removePeer_(id);
    } catch (const std::exception& error) {
        std::cerr << "[peer] failed to remove video-send peer id=" << id
                  << ": " << error.what() << std::endl;
    } catch (...) {
        std::cerr << "[peer] failed to remove video-send peer id=" << id
                  << ": unknown exception" << std::endl;
    }
}
