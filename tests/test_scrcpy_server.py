import re
from types import SimpleNamespace

import pytest

import server.scrcpy_server as scrcpy_server
from server.scrcpy_server import ScrcpyServerLauncher, build_scrcpy_args


def test_1080_arguments_allow_full_hd_without_lowering_bitrate_or_fps():
    args = build_scrcpy_args("1080", 1234)
    assert "max_size=1920" in args
    assert "video_bit_rate=8000000" in args
    assert "max_fps=60" in args


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


def test_failed_launch_cleans_up_only_its_server_and_forward_before_raising():
    events = []
    launcher = ScrcpyServerLauncher(
        "emulator-5556",
        1,
        find_adb=lambda: "adb",
        start_server=lambda *args: events.append(("start", args)) or False,
        stop_server=lambda *args: events.append(("stop", args)),
    )

    with pytest.raises(RuntimeError, match="failed to start server"):
        launcher.launch("1080", generation=4)

    expected_scope = ("adb", "emulator-5556", 27184, 1)
    expected_events = [
        ("start", (*expected_scope, "1080")),
        ("stop", expected_scope),
    ]
    assert events == expected_events

    launcher.stop()
    assert events == expected_events


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


def test_bitrate_is_sent_under_the_key_the_server_accepts():
    from server.scrcpy_server import build_scrcpy_args

    args = build_scrcpy_args("480", 1)

    assert "video_bit_rate=2000000" in args
    assert not any(arg.startswith("bit_rate=") for arg in args)


def test_every_argument_key_is_known_to_the_bundled_server():
    # scrcpy-server logs "Unknown server option" and drops a key it does not
    # know, so a misnamed option silently does nothing.
    import zipfile
    from pathlib import Path

    import pytest

    from server.scrcpy_server import build_scrcpy_args

    jar = Path(__file__).parent.parent / "src" / "assets" / "scrcpy" / "scrcpy-server"
    if not jar.exists():
        pytest.skip("bundled scrcpy-server is not downloaded")
    dex = zipfile.ZipFile(jar).read("classes.dex")
    for arg in build_scrcpy_args("720", 1):
        key = arg.split("=", 1)[0]
        # A dex string is stored as <length><bytes>\0, which rules out a
        # match inside a longer name.
        assert bytes([len(key)]) + key.encode() + b"\x00" in dex, key
