#include "remote_peer_handler.h"

RemotePeerHandler::RemotePeerHandler(PeerRegistry& registry, ScrcpySource& source, InputRouter& inputRouter, const EngineConfig::AuthConfig& authConfig)
    : registry_(registry), source_(source), inputRouter_(inputRouter), authConfig_(authConfig) {
}

RemotePeerHandler::~RemotePeerHandler() = default;

void RemotePeerHandler::operator()(const httplib::Request& req, httplib::Response& res) {
}
