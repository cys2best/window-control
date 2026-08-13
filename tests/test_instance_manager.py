import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from server.instance_manager import InstanceManager
from server.mediamtx_manager import MediamtxManager


def test_set_tier_unknown_serial_false():
    im = InstanceManager(MediamtxManager())
    assert im.set_tier("emulator-9999", "1080") is False
