import pytest

from server.network_gate import is_allowed_peer, is_loopback_peer


@pytest.mark.parametrize("host", [
    "127.0.0.1", "127.8.9.10",
    "10.0.0.5",
    "172.16.0.1", "172.31.255.254",
    "192.168.1.50",
    "169.254.10.10",
    "100.64.0.1", "100.127.255.254",
    "::1",
    "fd7a:115c:a1e0::1",
    "fe80::1", "fe80::1%en0",
    "::ffff:192.168.1.50",
])
def test_private_and_tailscale_peers_are_allowed(host):
    assert is_allowed_peer(host) is True


@pytest.mark.parametrize("host", [
    "8.8.8.8", "203.0.113.9",
    "172.15.255.255", "172.32.0.1",
    "100.63.255.255", "100.128.0.1",
    "192.169.0.1",
    "2001:4860:4860::8888",
    "::ffff:8.8.8.8",
    "", None, "testclient", "not-an-ip", "192.168.1", "192.168.1.50:8080",
])
def test_everything_else_is_refused(host):
    assert is_allowed_peer(host) is False


@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", True),
    ("::1", True),
    ("::ffff:127.0.0.1", True),
    ("192.168.1.50", False),
    ("100.64.0.1", False),
    ("localhost", False),
    (None, False),
])
def test_loopback_detection(host, expected):
    assert is_loopback_peer(host) is expected
