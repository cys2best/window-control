import pytest

from server.scrcpy_server import ScrcpyServerLauncher


def test_launch_starts_server_without_opening_media_sockets():
    calls = []
    launcher = ScrcpyServerLauncher(
        "emulator-5554", 0,
        find_adb=lambda: "adb",
        start_server=lambda adb, serial, port, scid, tier:
            calls.append((adb, serial, port, scid, tier)) or True,
        stop_server=lambda adb, serial, port, scid: None,
    )

    launch = launcher.launch("720", generation=0)

    assert launch.port == 27183
    assert launch.generation == 0
    assert launch.tier == "720"
    assert calls == [("adb", "emulator-5554", 27183, 0, "720")]


def test_launch_rejects_invalid_tier_before_adb_call():
    launcher = ScrcpyServerLauncher(
        "emulator-5554", 0,
        find_adb=lambda: "adb",
        start_server=lambda *args: pytest.fail("must not launch"),
        stop_server=lambda *args: None,
    )
    with pytest.raises(ValueError, match="unknown quality tier"):
        launcher.launch("9000", generation=0)


def test_stop_removes_only_its_forward_and_server():
    stopped = []
    launcher = ScrcpyServerLauncher(
        "emulator-5556", 1,
        find_adb=lambda: "adb",
        start_server=lambda *args: True,
        stop_server=lambda *args: stopped.append(args),
    )
    launcher.launch("1080", generation=4)
    launcher.stop()
    assert stopped == [("adb", "emulator-5556", 27184, 1)]
