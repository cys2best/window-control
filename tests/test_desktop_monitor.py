# tests/test_desktop_monitor.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from unittest.mock import patch, MagicMock
from service.desktop_monitor import get_current_desktop_name

def test_get_desktop_name_returns_string():
    # On non-Windows, stub returns "Default"
    name = get_current_desktop_name()
    assert isinstance(name, str)
    assert len(name) > 0

def test_get_desktop_name_known_values():
    name = get_current_desktop_name()
    # Must be one of the known desktop names or a valid string
    assert name in ("Default", "Winlogon", "Screen-saver") or len(name) > 0
