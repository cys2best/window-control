"""Builds the ICE server list (STUN + optional TURN) for the *public*
WebRTC path (initWebRTCPublic() in the client).

The local/Tailscale path keeps using the embedded STUN_PORT server
(stun_server.py, bound to the Tailscale IP) unchanged -- this module only
serves the public path, which has no way to reach that Tailscale-bound
address. A NAT'd PC has no publicly reachable ICE candidate on its own;
TURN_HOST (the project's coturn instance) provides a relay candidate so
ICE can actually connect. TURN is optional: absent TURN_HOST, this
returns STUN-only, and public ICE will fail for any NAT'd PC (falling
back to local WHEP, unreachable off-Tailscale) -- same failure mode this
module exists to fix.
"""

import config


def get_ice_servers() -> list[dict]:
    servers = [{"urls": "stun:stun.l.google.com:19302"}]
    if config.TURN_HOST and config.TURN_USERNAME and config.TURN_CREDENTIAL:
        servers.append({
            "urls": f"turn:{config.TURN_HOST}:{config.TURN_PORT}",
            "username": config.TURN_USERNAME,
            "credential": config.TURN_CREDENTIAL,
        })
    return servers
