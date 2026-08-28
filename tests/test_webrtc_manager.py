import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import asyncio
import pytest
from aiortc import RTCIceServer

from server.webrtc_manager import WebrtcManager, _ice_servers_to_aiortc


def test_ice_servers_to_aiortc_stun_only():
    result = _ice_servers_to_aiortc([{"urls": "stun:stun.l.google.com:19302"}])
    assert result == [RTCIceServer(urls="stun:stun.l.google.com:19302")]


def test_ice_servers_to_aiortc_with_turn_credentials():
    result = _ice_servers_to_aiortc([
        {"urls": "turn:1.2.3.4:3478", "username": "u", "credential": "p"},
    ])
    assert result == [RTCIceServer(urls="turn:1.2.3.4:3478", username="u", credential="p")]
