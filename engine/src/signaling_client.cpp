#include "signaling_client.h"
#include <websocketpp/config/asio_client.hpp>
#include <websocketpp/client.hpp>
#include <thread>
#include <atomic>

using WsClient = websocketpp::client<websocketpp::config::asio_client>;

struct SignalingClient::Impl {
    std::string url;
    WsClient client;
    websocketpp::connection_hdl handle;
    std::thread ioThread;
    std::atomic<bool> connected{false};
    MessageCallback onMessage;

    ~Impl() {
        if (connected) {
            websocketpp::lib::error_code ec;
            client.close(handle, websocketpp::close::status::normal, "shutdown", ec);
        }
        client.stop();
        if (ioThread.joinable()) ioThread.join();
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
    : impl_(std::make_unique<Impl>()) {
    impl_->url = BuildUrl(wsUrl, sessionId, role, token);
}

SignalingClient::~SignalingClient() {
    Disconnect();
}

void SignalingClient::Connect(MessageCallback onMessage) {
    impl_->onMessage = std::move(onMessage);
    impl_->client.clear_access_channels(websocketpp::log::alevel::all);
    impl_->client.init_asio();

    impl_->client.set_open_handler([this](websocketpp::connection_hdl) {
        impl_->connected = true;
    });
    impl_->client.set_close_handler([this](websocketpp::connection_hdl) {
        impl_->connected = false;
    });
    impl_->client.set_message_handler([this](websocketpp::connection_hdl, WsClient::message_ptr msg) {
        if (impl_->onMessage) impl_->onMessage(msg->get_payload());
    });

    websocketpp::lib::error_code ec;
    auto con = impl_->client.get_connection(impl_->url, ec);
    if (ec) {
        throw std::runtime_error("SignalingClient: failed to create connection: " + ec.message());
    }
    impl_->handle = con->get_handle();
    impl_->client.connect(con);

    impl_->ioThread = std::thread([this]() { impl_->client.run(); });
}

void SignalingClient::Send(const std::string& jsonMessage) {
    websocketpp::lib::error_code ec;
    impl_->client.send(impl_->handle, jsonMessage, websocketpp::frame::opcode::text, ec);
}

void SignalingClient::Disconnect() {
    if (!impl_->connected.exchange(false)) return;
    websocketpp::lib::error_code ec;
    impl_->client.close(impl_->handle, websocketpp::close::status::normal, "shutdown", ec);
}

bool SignalingClient::IsConnected() const {
    return impl_->connected.load();
}
