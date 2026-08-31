import re
from types import SimpleNamespace

import pytest

import server.scrcpy_server as scrcpy_server
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


def test_server_cleanup_targets_only_the_selected_android_server_process(monkeypatch):
    """Catches a cleanup pattern that misses app_process Server or crosses scids."""
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(scrcpy_server.os.path, "exists", lambda _: True)
    monkeypatch.setattr(scrcpy_server.subprocess, "run", run)
    monkeypatch.setattr(scrcpy_server.subprocess, "Popen", lambda *args, **kwargs: None)
    monkeypatch.setattr(scrcpy_server.time, "sleep", lambda _: None)

    assert scrcpy_server.start_server("adb", "emulator-5554", 27183, 0, "720")
    scrcpy_server.stop_server("adb", "emulator-5554", 27183, 0)

    cleanup_commands = [
        command
        for command in commands
        if command[:4] == ["adb", "-s", "emulator-5554", "shell"]
        and command[4].startswith("pkill -f ")
    ]
    expected = "pkill -f 'com[.]genymobile[.]scrcpy[.]Server.*scid=0$'"
    assert cleanup_commands == [
        ["adb", "-s", "emulator-5554", "shell", expected],
        ["adb", "-s", "emulator-5554", "shell", expected],
    ]

    pattern = cleanup_commands[0][4].removeprefix("pkill -f '").removesuffix("'")
    observed_server = (
        "app_process / com.genymobile.scrcpy.Server 3.1 "
        "tunnel_forward=true video_codec=h264 scid=0"
    )
    cleanup_process = "app_process / com.genymobile.scrcpy.CleanUp 3.1 scid=0"
    other_server = "app_process / com.genymobile.scrcpy.Server 3.1 scid=1"
    assert re.search(pattern, observed_server)
    assert not re.search(pattern, cleanup_process)
    assert not re.search(pattern, other_server)
