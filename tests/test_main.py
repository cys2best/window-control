import sys
import pytest


def test_config_imports():
    """config.py exports expected constants including new mediamtx config."""
    from config import (PORT, VERSION, QUALITY_MAP, DEFAULT_QUALITY,
                        MEDIAMTX_PORT, WHEP_PORT, SCRCPY_PATH, MEDIAMTX_PATH,
                        CLIENT_DIR, ASSETS_DIR)
    assert PORT == 8080
    assert MEDIAMTX_PORT == 8554
    assert WHEP_PORT == 8889
    assert DEFAULT_QUALITY in QUALITY_MAP


def test_instance_name_emulator():
    from server.instance_manager import instance_name
    assert instance_name("emulator-5554") == "instance0"
    assert instance_name("emulator-5556") == "instance1"
    assert instance_name("emulator-5558") == "instance2"


def test_instance_name_non_emulator():
    from server.instance_manager import instance_name
    name = instance_name("192.168.1.100:5555")
    assert name.startswith("instance_")
