#include "signaling_client.h"
#include <websocketpp/config/asio_client.hpp>
#include <websocketpp/client.hpp>
#include <websocketpp/uri.hpp>
#include <asio/ssl/host_name_verification.hpp>
#include <openssl/err.h>
#include <openssl/ssl.h>
#include <openssl/x509.h>
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#include <wincrypt.h>
#include <thread>
#include <atomic>
#include <condition_variable>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <vector>

using PlainWsClient = websocketpp::client<websocketpp::config::asio_client>;
using TlsWsClient = websocketpp::client<websocketpp::config::asio_tls_client>;
using TlsContext = websocketpp::lib::asio::ssl::context;
using TlsContextPtr = websocketpp::lib::shared_ptr<TlsContext>;

namespace {
std::size_t AddWindowsRootStore(TlsContext& context, DWORD storeLocation) {
    HCERTSTORE certStore = CertOpenStore(
        CERT_STORE_PROV_SYSTEM_W,
        0,
        0,
        storeLocation | CERT_STORE_OPEN_EXISTING_FLAG | CERT_STORE_READONLY_FLAG,
        L"ROOT");
    if (!certStore) return 0;

    std::size_t added = 0;
    X509_STORE* trustStore = SSL_CTX_get_cert_store(context.native_handle());
    PCCERT_CONTEXT cert = nullptr;
    while ((cert = CertEnumCertificatesInStore(certStore, cert)) != nullptr) {
        const unsigned char* encoded = cert->pbCertEncoded;
        X509* root = d2i_X509(nullptr, &encoded, cert->cbCertEncoded);
        if (!root) {
            ERR_clear_error();
            continue;
        }
        if (X509_STORE_add_cert(trustStore, root) == 1) ++added;
        X509_free(root);
        // Duplicate roots are expected when OpenSSL's default paths and the
        // current-user/local-machine Windows stores overlap.
        ERR_clear_error();
    }
    CertCloseStore(certStore, 0);
    return added;
}

TlsContextPtr CreateTlsContext(const std::string& hostname) {
    auto context = websocketpp::lib::make_shared<TlsContext>(TlsContext::tls_client);
    context->set_options(
        TlsContext::default_workarounds |
        TlsContext::no_sslv2 |
        TlsContext::no_sslv3);
    if (SSL_CTX_set_min_proto_version(context->native_handle(), TLS1_2_VERSION) != 1) {
        throw std::runtime_error("could not require TLS 1.2 or newer");
    }

    context->set_verify_mode(websocketpp::lib::asio::ssl::verify_peer);
    websocketpp::lib::asio::error_code defaultPathsError;
    context->set_default_verify_paths(defaultPathsError);
    const auto windowsRoots =
        AddWindowsRootStore(*context, CERT_SYSTEM_STORE_CURRENT_USER) +
        AddWindowsRootStore(*context, CERT_SYSTEM_STORE_LOCAL_MACHINE);
    if (defaultPathsError && windowsRoots == 0) {
        throw std::runtime_error("no default or Windows root certificates were available");
    }
    context->set_verify_callback(
        websocketpp::lib::asio::ssl::host_name_verification(hostname));
    return context;
}
}

struct SignalingClient::Impl {
    std::string url;
    PlainWsClient plainClient;
    TlsWsClient tlsClient;
    bool secure = false;
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

    void SendText(const std::string& message, websocketpp::lib::error_code& ec) {
        if (secure) {
            tlsClient.send(handle, message, websocketpp::frame::opcode::text, ec);
        } else {
            plainClient.send(handle, message, websocketpp::frame::opcode::text, ec);
        }
    }

    void CloseTransport(websocketpp::lib::error_code& ec) {
        if (secure) {
            tlsClient.close(handle, websocketpp::close::status::normal, "shutdown", ec);
        } else {
            plainClient.close(handle, websocketpp::close::status::normal, "shutdown", ec);
        }
    }

    void StopTransport() {
        if (secure) {
            tlsClient.stop();
        } else {
            plainClient.stop();
        }
    }

    void RunTransport() {
        if (secure) {
            tlsClient.run();
        } else {
            plainClient.run();
        }
    }

    template <typename Client>
    void ConfigureConnection(Client& client, std::weak_ptr<Impl> weakImpl) {
        client.clear_access_channels(websocketpp::log::alevel::all);
        client.init_asio();
        asioInitialized = true;

        client.set_open_handler([weakImpl](websocketpp::connection_hdl) {
            auto state = weakImpl.lock();
            if (!state || state->disconnectRequested.load()) return;
            state->connected.store(true);
            if (state->disconnectRequested.load()) {
                state->connected.store(false);
                return;
            }
            state->FlushPending();
        });
        client.set_close_handler([weakImpl](websocketpp::connection_hdl) {
            if (auto state = weakImpl.lock()) state->connected.store(false);
        });
        client.set_message_handler(
            [weakImpl](websocketpp::connection_hdl, typename Client::message_ptr msg) {
                if (auto state = weakImpl.lock()) {
                    state->DispatchMessage(msg->get_payload());
                }
            });

        websocketpp::lib::error_code ec;
        auto connection = client.get_connection(url, ec);
        if (ec) {
            throw std::runtime_error(
                "SignalingClient: failed to create connection: " + ec.message());
        }
        handle = connection->get_handle();
        client.connect(connection);
    }

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
            SendText(msg, ec);
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
        if (asioInitialized) StopTransport();
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
    try {
        websocketpp::uri uri(impl->url);
        if (!uri.get_valid()) {
            throw std::runtime_error(
                "SignalingClient: failed to create connection: invalid URI");
        }
        impl->secure = uri.get_secure();
        std::weak_ptr<Impl> weakImpl = impl;
        if (impl->secure) {
            const std::string hostname = uri.get_host();
            impl->tlsClient.set_tls_init_handler(
                [hostname](websocketpp::connection_hdl) {
                    return CreateTlsContext(hostname);
                });
            // websocketpp 0.8.2's TLS transport applies the URI hostname as
            // SNI before its TLS handshake. The context callback above adds
            // independent RFC 6125 hostname verification for the certificate.
            impl->ConfigureConnection(impl->tlsClient, weakImpl);
        } else {
            impl->ConfigureConnection(impl->plainClient, weakImpl);
        }
    } catch (...) {
        impl->disconnectRequested.store(true);
        impl->shutdownRequested = true;
        impl->quiesced = true;
        impl->SuppressCallbacks();
        if (impl->asioInitialized) impl->StopTransport();
        throw;
    }

    try {
        impl->ioThread = std::thread([impl]() {
            impl->RunTransport();
            impl->connected.store(false);
        });
        impl->ioThreadId = impl->ioThread.get_id();
    } catch (...) {
        impl->disconnectRequested.store(true);
        impl->shutdownRequested = true;
        impl->quiesced = true;
        impl->SuppressCallbacks();
        if (impl->asioInitialized) impl->StopTransport();
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
    impl->SendText(jsonMessage, ec);
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
            impl->CloseTransport(ec);
        }
        if (impl->asioInitialized) impl->StopTransport();
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
