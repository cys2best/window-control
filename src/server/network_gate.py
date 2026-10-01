"""Source-address allowlist: local network and Tailscale peers only."""

import ipaddress

_ALLOWED_NETWORKS = tuple(ipaddress.ip_network(cidr) for cidr in (
    "127.0.0.0/8",
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "100.64.0.0/10",   # Tailscale (CGNAT range)
    "::1/128",
    "fc00::/7",
    "fe80::/10",
))


def _parse(host: str | None):
    if not host:
        return None
    try:
        address = ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def is_allowed_peer(host: str | None) -> bool:
    address = _parse(host)
    return address is not None and any(address in network for network in _ALLOWED_NETWORKS)


def is_loopback_peer(host: str | None) -> bool:
    address = _parse(host)
    return address is not None and address.is_loopback
