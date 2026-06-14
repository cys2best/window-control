# tests/test_pipe_protocol.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import json
from service.pipe_server import encode_msg, decode_msg

def test_encode_decode_roundtrip():
    msg = {"cmd": "select", "id": 99999}
    encoded = encode_msg(msg)
    assert encoded.endswith(b"\n")
    decoded = decode_msg(encoded.rstrip(b"\n"))
    assert decoded == msg

def test_encode_event():
    msg = {"event": "state", "streaming": True, "locked": False}
    encoded = encode_msg(msg)
    assert b"streaming" in encoded

def test_decode_invalid_returns_none():
    assert decode_msg(b"not json") is None

def test_decode_empty_returns_none():
    assert decode_msg(b"") is None
