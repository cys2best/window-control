import re
import socket
import subprocess


def detect_tailscale_ip():
    try:
        output = subprocess.check_output(
            ["ipconfig", "/all"], text=True, stderr=subprocess.DEVNULL
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        # ipconfig not available on Mac
        return None
    # Tailscale uses CGNAT 100.64.0.0/10 (second octet 64-127); Windows only
    match = re.search(r'\b100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}\b', output)
    return match.group(0) if match else None


def detect_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def get_best_ip():
    """Return Tailscale IP if available, else LAN IP."""
    return detect_tailscale_ip() or detect_local_ip()


def has_tailscale():
    return detect_tailscale_ip() is not None
