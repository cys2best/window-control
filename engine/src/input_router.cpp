// engine/src/input_router.cpp
// Canonical input message shapes match src/client/app.js's sendInput()
// calls and src/server/app.py's WebSocket input handler exactly, ported
// to per-peer WebRTC DataChannels instead of one shared WebSocket.
#include "input_router.h"
#include <algorithm>
#include <cmath>
#include <nlohmann/json.hpp>
#include <optional>
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

std::optional<double> FiniteNumber(const json& message, const char* field) {
    auto it = message.find(field);
    if (it == message.end() || !it->is_number() || it->is_boolean()) {
        return std::nullopt;
    }
    const double value = it->get<double>();
    return std::isfinite(value) ? std::optional<double>(value) : std::nullopt;
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
        FingerState finger;
        bool releaseFinger = false;
        {
            std::lock_guard<std::mutex> lock(fingerMutex_);
            auto it = fingerStates_.find(&peer);
            if (it != fingerStates_.end()) {
                finger = it->second;
                releaseFinger = finger.down;
                fingerStates_.erase(it);
            }
        }
        if (releaseFinger) {
            if (auto control = source_.Control()) {
                auto status = source_.Status();
                control->SendTouch(ScrcpyControlClient::ACTION_UP, finger.x, finger.y,
                                    status.width, status.height, finger.pointerId);
            }
        }
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

    const bool hasCoordinates =
        type == "click" || type == "drag_start" || type == "drag_move" ||
        type == "drag_end" || type == "scroll";
    std::optional<double> x;
    std::optional<double> y;
    if (hasCoordinates) {
        x = FiniteNumber(msg, "x");
        y = FiniteNumber(msg, "y");
        if (!x || !y) return;
        *x = std::clamp(*x, 0.0, 1.0);
        *y = std::clamp(*y, 0.0, 1.0);
    }

    if (type == "click") {
        {
            std::lock_guard<std::mutex> lock(fingerMutex_);
            fingerStates_.erase(peer);
        }
        control->SendTouch(
            ScrcpyControlClient::ACTION_DOWN, *x, *y, status.width, status.height);
        control->SendTouch(
            ScrcpyControlClient::ACTION_UP, *x, *y, status.width, status.height);
    } else if (type == "drag_start") {
        {
            std::lock_guard<std::mutex> lock(fingerMutex_);
            fingerStates_[peer] = FingerState{true, 0, *x, *y};
        }
        control->SendTouch(
            ScrcpyControlClient::ACTION_DOWN, *x, *y, status.width, status.height);
    } else if (type == "drag_move") {
        std::uint64_t pointerId = 0;
        bool moveFinger = false;
        {
            std::lock_guard<std::mutex> lock(fingerMutex_);
            auto it = fingerStates_.find(peer);
            if (it != fingerStates_.end() && it->second.down) {
                it->second.x = *x;
                it->second.y = *y;
                pointerId = it->second.pointerId;
                moveFinger = true;
            }
        }
        if (moveFinger) {
            control->SendTouch(
                ScrcpyControlClient::ACTION_MOVE, *x, *y,
                status.width, status.height, pointerId);
        }
    } else if (type == "drag_end") {
        std::uint64_t pointerId = 0;
        bool releaseFinger = false;
        {
            std::lock_guard<std::mutex> lock(fingerMutex_);
            auto it = fingerStates_.find(peer);
            if (it != fingerStates_.end()) {
                pointerId = it->second.pointerId;
                releaseFinger = it->second.down;
                fingerStates_.erase(it);
            }
        }
        if (releaseFinger) {
            control->SendTouch(
                ScrcpyControlClient::ACTION_UP, *x, *y,
                status.width, status.height, pointerId);
        }
    } else if (type == "scroll") {
        const auto dy = FiniteNumber(msg, "dy");
        if (!dy) return;

        FingerState activeFinger;
        bool cancelDrag = false;
        {
            std::lock_guard<std::mutex> lock(fingerMutex_);
            auto it = fingerStates_.find(peer);
            if (it != fingerStates_.end()) {
                activeFinger = it->second;
                cancelDrag = activeFinger.down;
                fingerStates_.erase(it);
            }
        }
        if (cancelDrag) {
            control->SendTouch(
                ScrcpyControlClient::ACTION_UP, activeFinger.x, activeFinger.y,
                status.width, status.height, activeFinger.pointerId);
        }

        const double delta = std::clamp(*dy, -0.25, 0.25);
        const double targetY = std::clamp(*y + delta, 0.0, 1.0);
        control->SendTouch(
            ScrcpyControlClient::ACTION_DOWN, *x, *y, status.width, status.height);
        control->SendTouch(
            ScrcpyControlClient::ACTION_MOVE, *x, targetY, status.width, status.height);
        control->SendTouch(
            ScrcpyControlClient::ACTION_UP, *x, targetY, status.width, status.height);
    } else if (type == "key") {
        std::int32_t keycode = KeycodeForKey(msg.value("key", ""));
        if (keycode != 0) control->SendKeycode(keycode);
    } else if (type == "idr") {
        auto now = std::chrono::steady_clock::now();
        {
            std::lock_guard<std::mutex> lock(idrMutex_);
            if (now - lastIdrRequest_ < idrRateLimit_) return;
            lastIdrRequest_ = now;
        }
        control->RequestIdr();
    }
}
