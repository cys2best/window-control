#include "signaling_client.h"
#include <websocketpp/config/asio_client.hpp>
#include <websocketpp/client.hpp>
#include <thread>
#include <atomic>
#include <condition_variable>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <vector>

using WsClient = websocketpp::client<websocketpp::config::asio_client>;

struct SignalingClient::Impl {
    std::string url;
    WsClient client;
    websocketpp::connection_hdl handle;
    std::thread ioThread;
    std::thread::id ioThreadId;
    std::atomic<bool> connected{false};
    std::atomic<bool> disconnectRequested{false};

    std::mutex lifecycleMutex;
    std::condition_variable lifecycleCv;
    bool connectStarted = false;
    bool asioInitialized = false;
    bool shutdownRequested = false;
    bool joinInProgress = false;
    bool quiesced = false;

    std::mutex callbackMutex;
    bool callbacksSuppressed = true;
    MessageCallback onMessage;

    // setLocalDescription()'s offer/candidates fire on the caller's thread
    // essentially synchronously — long before the WS handshake to a remote
    // signaling server completes on ioThread. Without this queue, every
    // Send() call made in that window silently fails ("invalid state") and
    // the offer is lost for good; the peer then sits in ICE gathering
    // forever with nothing sent to the other side.
    std::mutex pendingMutex;
    std::vector<std::string> pending;

    void SetMessageCallback(MessageCallback callback) {
        std::lock_guard<std::mutex> lock(callbackMutex);
        callbacksSuppressed = false;
        onMessage = std::move(callback);
    }

    void SuppressCallbacks() {
        std::lock_guard<std::mutex> lock(callbackMutex);
        callbacksSuppressed = true;
        onMessage = {};
    }

    void DispatchMessage(const std::string& payload) {
        MessageCallback callback;
        {
            std::lock_guard<std::mutex> lock(callbackMutex);
            if (callbacksSuppressed) return;
            callback = onMessage;
        }
        if (callback) callback(payload);
    }

    void FlushPending() {
        std::vector<std::string> toSend;
        {
            std::lock_guard<std::mutex> lock(pendingMutex);
            toSend.swap(pending);
        }
        std::cerr << "[debug] SignalingClient: flushing " << toSend.size() << " queued message(s)" << std::endl;
        for (auto& msg : toSend) {
            if (disconnectRequested.load()) break;
            websocketpp::lib::error_code ec;
            client.send(handle, msg, websocketpp::frame::opcode::text, ec);
            if (ec) {
                std::cerr << "[debug] SignalingClient: flush send failed: " << ec.message() << std::endl;
            } else {
                std::cerr << "[debug] SignalingClient: flush sent " << msg.size() << " bytes ok" << std::endl;
            }
        }
    }

    ~Impl() {
        disconnectRequested.store(true);
        connected.store(false);
        if (asioInitialized) client.stop();
        if (!ioThread.joinable()) return;
        if (ioThread.get_id() == std::this_thread::get_id()) {
            ioThread.detach();
        } else {
            ioThread.join();
        }
    }
};

namespace {
std::string BuildUrl(const std::string& base, const std::string& session,
                      const std::string& role, const std::string& token) {
    std::string url = base + "/?session=" + session + "&role=" + role;
    if (!token.empty()) url += "&token=" + token;
    return url;
}
}

SignalingClient::SignalingClient(const std::string& wsUrl, const std::string& sessionId,
                                   const std::string& role, const std::string& token)
    : impl_(std::make_shared<Impl>()) {
    impl_->url = BuildUrl(wsUrl, sessionId, role, token);
}

SignalingClient::~SignalingClient() {
    Disconnect();
}

void SignalingClient::Connect(MessageCallback onMessage) {
    auto impl = impl_;
    std::lock_guard<std::mutex> lifecycleLock(impl->lifecycleMutex);
    if (impl->connectStarted || impl->shutdownRequested) {
        throw std::logic_error(
            "SignalingClient: Connect may only be called once before shutdown");
    }
    impl->connectStarted = true;
    impl->disconnectRequested.store(false);
    impl->SetMessageCallback(std::move(onMessage));
    impl->client.clear_access_channels(websocketpp::log::alevel::all);
    impl->client.init_asio();
    impl->asioInitialized = true;

    std::weak_ptr<Impl> weakImpl = impl;
    impl->client.set_open_handler([weakImpl](websocketpp::connection_hdl) {
        auto state = weakImpl.lock();
        if (!state || state->disconnectRequested.load()) return;
        state->connected.store(true);
        if (state->disconnectRequested.load()) {
            state->connected.store(false);
            return;
        }
        state->FlushPending();
    });
    impl->client.set_close_handler([weakImpl](websocketpp::connection_hdl) {
        if (auto state = weakImpl.lock()) state->connected.store(false);
    });
    impl->client.set_message_handler([weakImpl](websocketpp::connection_hdl, WsClient::message_ptr msg) {
        if (auto state = weakImpl.lock()) state->DispatchMessage(msg->get_payload());
    });

    websocketpp::lib::error_code ec;
    auto con = impl->client.get_connection(impl->url, ec);
    if (ec) {
        impl->disconnectRequested.store(true);
        impl->shutdownRequested = true;
        impl->quiesced = true;
        impl->SuppressCallbacks();
        if (impl->asioInitialized) impl->client.stop();
        throw std::runtime_error("SignalingClient: failed to create connection: " + ec.message());
    }
    impl->handle = con->get_handle();
    impl->client.connect(con);

    try {
        impl->ioThread = std::thread([impl]() {
            impl->client.run();
            impl->connected.store(false);
        });
        impl->ioThreadId = impl->ioThread.get_id();
    } catch (...) {
        impl->disconnectRequested.store(true);
        impl->shutdownRequested = true;
        impl->quiesced = true;
        impl->SuppressCallbacks();
        if (impl->asioInitialized) impl->client.stop();
        throw;
    }
}

void SignalingClient::Send(const std::string& jsonMessage) {
    auto impl = impl_;
    if (impl->disconnectRequested.load()) return;
    // Queue instead of dropping when the WS handshake hasn't completed yet —
    // see the comment on Impl::pending. Once connected, send directly; a
    // send-while-connected failure is a real transport error, not a timing
    // gap, so it's still just logged rather than retried.
    if (!impl->connected.load()) {
        std::lock_guard<std::mutex> lock(impl->pendingMutex);
        // Re-check under the lock: the open handler may have flushed and
        // flipped `connected` between the load above and taking this lock.
        if (impl->disconnectRequested.load()) return;
        if (!impl->connected.load()) {
            impl->pending.push_back(jsonMessage);
            return;
        }
    }

    websocketpp::lib::error_code ec;
    impl->client.send(impl->handle, jsonMessage, websocketpp::frame::opcode::text, ec);
    if (ec) {
        std::cerr << "[debug] SignalingClient::Send failed: " << ec.message() << std::endl;
    }
}

void SignalingClient::Disconnect() {
    auto impl = impl_;
    std::unique_lock<std::mutex> lifecycleLock(impl->lifecycleMutex);

    if (!impl->shutdownRequested) {
        impl->shutdownRequested = true;
        impl->disconnectRequested.store(true);
        bool wasConnected = impl->connected.exchange(false);
        impl->SuppressCallbacks();
        {
            std::lock_guard<std::mutex> pendingLock(impl->pendingMutex);
            impl->pending.clear();
        }
        if (wasConnected) {
            websocketpp::lib::error_code ec;
            impl->client.close(
                impl->handle, websocketpp::close::status::normal, "shutdown", ec);
        }
        if (impl->asioInitialized) impl->client.stop();
    }

    if (std::this_thread::get_id() == impl->ioThreadId) return;
    if (impl->joinInProgress) {
        impl->lifecycleCv.wait(lifecycleLock, [&]() { return impl->quiesced; });
        return;
    }
    if (!impl->ioThread.joinable()) {
        impl->quiesced = true;
        return;
    }

    impl->joinInProgress = true;
    lifecycleLock.unlock();
    impl->ioThread.join();
    lifecycleLock.lock();
    impl->joinInProgress = false;
    impl->quiesced = true;
    impl->lifecycleCv.notify_all();
}

bool SignalingClient::IsConnected() const {
    return impl_->connected.load();
}
