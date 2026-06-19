import sys
import pytest
from unittest.mock import patch, MagicMock


def test_list_vms_empty_when_adb_missing():
    """list_vms() returns [] if adb not found."""
    with patch("server.adb_manager._find_adb", return_value=None):
        from server import adb_manager
        result = adb_manager.list_vms()
    assert result == []


def test_list_vms_parses_emulator_serials():
    """list_vms() correctly parses emulator-5554/5556 serials."""
    adb_output = "List of devices attached\nemulator-5554\tdevice\nemulator-5556\tdevice\n"
    with patch("server.adb_manager._find_adb", return_value="/fake/adb"), \
         patch("server.adb_manager._get_dnplayer_titles", return_value=[]), \
         patch("subprocess.check_output", return_value=adb_output):
        from server import adb_manager
        result = adb_manager.list_vms()
    assert len(result) == 2
    assert result[0]["id"] == "adb:emulator-5554"
    assert result[0]["ldplayer_index"] == 0
    assert result[1]["id"] == "adb:emulator-5556"
    assert result[1]["ldplayer_index"] == 1


def test_instance_manager_list():
    """InstanceManager.list_instances() returns expected structure."""
    from server.instance_manager import InstanceManager
    mediamtx = MagicMock()
    with patch("server.instance_manager.adb_manager.list_vms", return_value=[]):
        im = InstanceManager(mediamtx)
    result = im.list_instances()
    assert isinstance(result, list)
