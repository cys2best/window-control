// engine/src/input_router.cpp
// Canonical input message shapes match src/client/app.js's sendInput()
// calls and src/server/app.py's WebSocket input handler exactly, ported
// to per-peer WebRTC DataChannels instead of one shared WebSocket.
#include "input_router.h"
#include <nlohmann/json.hpp>
#include <iostream>
#include <unordered_map>

using json = nlohmann::json;

namespace {

// Copied verbatim from src/server/app.py's _JS_KEY_TO_KEYCODE so both
// sides agree on every mapping.
const std::unordered_map<std::string, std::int32_t>& KeyTable() {
    static const std::unordered_map<std::string, std::int32_t> table = {
        {"Return", 66}, {"BackSpace", 67}, {"Tab", 61}, {"Escape", 111},
        {"Delete", 112}, {"ArrowLeft", 21}, {"ArrowUp", 19}, {"ArrowRight", 22},
        {"ArrowDown", 20}, {" ", 62}, {"Space", 62}, {"Back", 4}, {"Home", 3},
        {"Menu", 82},
    };
    return table;
}

} // namespace

InputRouter::InputRouter(ScrcpySource& source, std::chrono::milliseconds idrRateLimit)
    : source_(source), idrRateLimit_(idrRateLimit) {}

void InputRouter::AttachToPeer(PeerSession& peer) {
    peer.SetInputCallback([this, &peer](const std::string& jsonMessage) {
        HandleMessage(&peer, jsonMessage);
    });
    peer.SetOnStateChange([this, &peer](rtc::PeerConnection::State state) {
        if (state != rtc::PeerConnection::State::Disconnected &&
            state != rtc::PeerConnection::State::Failed &&
            state != rtc::PeerConnection::State::Closed) {
            return;
        }
        // Best-effort UP so one abruptly-disconnected viewer's held-down
        // finger doesn't stay stuck for whoever connects next.
        std::lock_guard<std::mutex> lock(fingerMutex_);
        auto it = fingerStates_.find(&peer);
        if (it != fingerStates_.end() && it->second.down) {
            if (auto control = source_.Control()) {
                auto status = source_.Status();
                control->SendTouch(ScrcpyControlClient::ACTION_UP, 0, 0,
                                    status.width, status.height, it->second.pointerId);
            }
        }
        fingerStates_.erase(it);
    });
}

void InputRouter::HandleMessageForTest(const std::string& jsonMessage) {
    HandleMessage(nullptr, jsonMessage);
}

std::int32_t InputRouter::KeycodeForKey(const std::string& key) const {
    auto it = KeyTable().find(key);
    return it != KeyTable().end() ? it->second : 0;
}

void InputRouter::HandleMessage(PeerSession* peer, const std::string& jsonMessage) {
    auto msg = json::parse(jsonMessage, nullptr, false);
    if (msg.is_discarded()) return;
    std::string type = msg.value("type", "");

    if (type == "echo") {
        if (peer) peer->SendInputMessage(jsonMessage);
        return;
    }

    auto control = source_.Control();
    auto status = source_.Status();
    if (!control) return;

    double x = msg.value("x", 0.0);
    double y = msg.value("y", 0.0);

    if (type == "click") {
        control->SendTouch(ScrcpyControlClient::ACTION_DOWN, x, y, status.width, status.height);
        control->SendTouch(ScrcpyControlClient::ACTION_UP, x, y, status.width, status.height);
    } else if (type == "drag_start") {
        std::lock_guard<std::mutex> lock(fingerMutex_);
        fingerStates_[peer] = FingerState{true, 0};
        control->SendTouch(ScrcpyControlClient::ACTION_DOWN, x, y, status.width, status.height);
    } else if (type == "drag_move") {
        control->SendTouch(ScrcpyControlClient::ACTION_MOVE, x, y, status.width, status.height);
    } else if (type == "drag_end") {
        {
            std::lock_guard<std::mutex> lock(fingerMutex_);
            fingerStates_.erase(peer);
        }
        control->SendTouch(ScrcpyControlClient::ACTION_UP, x, y, status.width, status.height);
    } else if (type == "scroll") {
        // Treated as a fast synthetic drag for this plan's C++ scope (see
        // Task 8's Interfaces note) — DOWN/MOVE(offset by dy)/UP.
        double dy = msg.value("dy", 0.0);
        control->SendTouch(ScrcpyControlClient::ACTION_DOWN, x, y, status.width, status.height);
        control->SendTouch(ScrcpyControlClient::ACTION_MOVE, x, y - dy, status.width, status.height);
        control->SendTouch(ScrcpyControlClient::ACTION_UP, x, y - dy, status.width, status.height);
    } else if (type == "key") {
        std::int32_t keycode = KeycodeForKey(msg.value("key", ""));
        if (keycode != 0) control->SendKeycode(keycode);
    } else if (type == "idr") {
        auto now = std::chrono::steady_clock::now();
        if (now - lastIdrRequest_ < idrRateLimit_) return;
        lastIdrRequest_ = now;
        control->RequestIdr();
    }
}
