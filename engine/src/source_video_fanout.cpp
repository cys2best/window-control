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
    MarkPeerFailed markPeerFailed)
    : preparer_(preparer),
      snapshotProvider_(std::move(snapshotProvider)),
      markPeerFailed_(std::move(markPeerFailed)) {}

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
    std::vector<std::string> failedPeerIds;
    for (const auto& peer : peers) {
        try {
            peer.send(sendData, sendSize);
        } catch (const std::exception& error) {
            std::cerr << "[peer] video send failed id=" << peer.id
                      << ": " << error.what() << std::endl;
            failedPeerIds.push_back(peer.id);
        } catch (...) {
            std::cerr << "[peer] video send failed id=" << peer.id
                      << ": unknown exception" << std::endl;
            failedPeerIds.push_back(peer.id);
        }
    }

    for (const auto& id : failedPeerIds) MarkFailedPeer(id);
}

void SourceVideoFanout::MarkFailedPeer(const std::string& id) {
    try {
        markPeerFailed_(id);
    } catch (const std::exception& error) {
        std::cerr << "[peer] failed to mark video-send peer id=" << id
                  << ": " << error.what() << std::endl;
    } catch (...) {
        std::cerr << "[peer] failed to mark video-send peer id=" << id
                  << ": unknown exception" << std::endl;
    }
}
