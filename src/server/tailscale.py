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
    match = re.search(r'100\.\d{1,3}\.\d{1,3}\.\d{1,3}', output)
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
