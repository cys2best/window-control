"""
Minimal STUN Binding server (RFC 5389), bound to the Tailscale interface.

Why this exists: Safari only emits an mDNS (.local) host candidate and offers
no flag to disable it. The Windows streaming host cannot resolve .local over
Tailscale, so the candidate pair never forms and media never flows.

Pointing the browser at a *public* STUN server (Google) does not help either:
the STUN query exits via the public internet, so the reflexive (srflx)
candidate reflects the ISP/WARP public IP (e.g. 104.28.x.x), which the
Tailscale-only host cannot reach.

Binding this STUN server to the *Tailscale IP* fixes it: the browser's query
routes over Tailscale, so the source address this server sees — and returns as
XOR-MAPPED-ADDRESS — is the browser's Tailscale IP. That yields an srflx
candidate the engine can reach directly, with no relay hop.

Only STUN Binding requests are handled — enough for ICE candidate discovery.
"""

import os
import socket
import struct
import threading

_MAGIC_COOKIE = 0x2112A442
_BINDING_REQUEST = 0x0001
_BINDING_RESPONSE = 0x0101
_ATTR_MAPPED_ADDRESS = 0x0001
_ATTR_XOR_MAPPED_ADDRESS = 0x0020
_FAMILY_IPV4 = 0x01


def _log(msg: str):
    for _p in [r"C:\ProgramData\EmuCtrl", r"C:\Windows\Temp", r"C:\Temp", "/tmp"]:
        try:
            os.makedirs(_p, exist_ok=True)
            with open(os.path.join(_p, "service_crash.log"), "a") as f:
                f.write(msg + "\n")
            return
        except Exception:
            continue


def _pack_mapped_address(attr_type: int, ip: str, port: int, txid: bytes) -> bytes:
    ip_int = struct.unpack("!I", socket.inet_aton(ip))[0]
    if attr_type == _ATTR_XOR_MAPPED_ADDRESS:
        xport = port ^ (_MAGIC_COOKIE >> 16)
        xip = ip_int ^ _MAGIC_COOKIE
        value = struct.pack("!BBHI", 0, _FAMILY_IPV4, xport, xip)
    else:
        value = struct.pack("!BBHI", 0, _FAMILY_IPV4, port, ip_int)
    return struct.pack("!HH", attr_type, len(value)) + value


def _handle_binding_request(data: bytes, addr) -> bytes | None:
    if len(data) < 20:
        return None
    msg_type, msg_len, cookie = struct.unpack("!HHI", data[:8])
    if msg_type != _BINDING_REQUEST or cookie != _MAGIC_COOKIE:
        return None
    txid = data[8:20]
    src_ip, src_port = addr[0], addr[1]

    # Include both XOR-MAPPED (RFC 5389) and MAPPED (RFC 3489) for wide client
    # compatibility.
    attrs = (
        _pack_mapped_address(_ATTR_XOR_MAPPED_ADDRESS, src_ip, src_port, txid)
        + _pack_mapped_address(_ATTR_MAPPED_ADDRESS, src_ip, src_port, txid)
    )
    header = struct.pack("!HHI", _BINDING_RESPONSE, len(attrs), _MAGIC_COOKIE) + txid
    return header + attrs


class StunServer:
    """Threaded UDP STUN Binding server bound to a specific host/port."""

    def __init__(self, host: str, port: int = 3478):
        self._host = host
        self._port = port
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self):
        if self._running:
            return
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self._host, self._port))
        except Exception as e:
            _log(f"[stun] bind failed on {self._host}:{self._port}: {e}")
            return
        self._sock = sock
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        _log(f"[stun] listening on {self._host}:{self._port}")

    def _loop(self):
        sock = self._sock
        while self._running and sock:
            try:
                data, addr = sock.recvfrom(2048)
            except OSError:
                break
            except Exception:
                continue
            try:
                resp = _handle_binding_request(data, addr)
                if resp:
                    sock.sendto(resp, addr)
            except Exception:
                continue

    def stop(self):
        self._running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
