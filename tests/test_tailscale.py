import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from unittest.mock import patch
from server.tailscale import detect_tailscale_ip, detect_local_ip

def test_detect_tailscale_ip_found():
    fake_output = (
        "Ethernet adapter Tailscale:\n"
        "   IPv4 Address. . . : 100.64.1.42\n"
    )
    with patch('server.tailscale.subprocess.check_output', return_value=fake_output):
        ip = detect_tailscale_ip()
    assert ip == "100.64.1.42"

def test_detect_tailscale_ip_not_found():
    fake_output = "Ethernet adapter Local:\n   IPv4 Address: 192.168.1.10\n"
    with patch('server.tailscale.subprocess.check_output', return_value=fake_output):
        ip = detect_tailscale_ip()
    assert ip is None

def test_detect_local_ip_returns_string():
    ip = detect_local_ip()
    assert isinstance(ip, str)
    assert len(ip) > 0
