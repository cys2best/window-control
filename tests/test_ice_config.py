import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import importlib
import config
from server import ice_config


def test_get_ice_servers_stun_only(monkeypatch):
    monkeypatch.delenv("TURN_HOST", raising=False)
    importlib.reload(config)
    importlib.reload(ice_config)
    servers = ice_config.get_ice_servers()
    assert servers == [{"urls": "stun:stun.l.google.com:19302"}]


def test_get_ice_servers_with_turn(monkeypatch):
    monkeypatch.setenv("TURN_HOST", "turn.example.com")
    monkeypatch.setenv("TURN_PORT", "3478")
    monkeypatch.setenv("TURN_USERNAME", "wcuser")
    monkeypatch.setenv("TURN_CREDENTIAL", "wcsecret")
    importlib.reload(config)
    importlib.reload(ice_config)
    servers = ice_config.get_ice_servers()
    assert servers == [
        {"urls": "stun:stun.l.google.com:19302"},
        {
            "urls": "turn:turn.example.com:3478",
            "username": "wcuser",
            "credential": "wcsecret",
        },
    ]


def test_get_ice_servers_turn_host_without_credentials_omits_turn(monkeypatch):
    monkeypatch.setenv("TURN_HOST", "turn.example.com")
    monkeypatch.delenv("TURN_USERNAME", raising=False)
    monkeypatch.delenv("TURN_CREDENTIAL", raising=False)
    importlib.reload(config)
    importlib.reload(ice_config)
    servers = ice_config.get_ice_servers()
    assert servers == [{"urls": "stun:stun.l.google.com:19302"}]
