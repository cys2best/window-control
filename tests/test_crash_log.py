import tempfile
from pathlib import Path

import crash_log
from crash_log import CrashLog, default_dirs


def test_default_dirs_off_windows_use_the_system_temp_dir(monkeypatch):
    monkeypatch.setattr(crash_log.sys, "platform", "darwin")

    dirs = default_dirs()

    assert dirs == [str(Path(tempfile.gettempdir()) / "EmuCtrl")]


def test_default_dirs_on_windows_prefer_programdata(monkeypatch):
    monkeypatch.setattr(crash_log.sys, "platform", "win32")
    monkeypatch.setenv("PROGRAMDATA", r"D:\Data")

    dirs = default_dirs()

    assert dirs[0] == str(Path(r"D:\Data") / "EmuCtrl")
    assert dirs[-1] == str(Path(tempfile.gettempdir()) / "EmuCtrl")


def test_write_appends_the_message_to_the_first_writable_dir(tmp_path):
    log = CrashLog([str(tmp_path / "a"), str(tmp_path / "b")])

    log.write("[engine] started")
    log.write("[engine] stopped")

    text = (tmp_path / "a" / "service_crash.log").read_text()
    assert "[engine] started" in text
    assert text.index("[engine] started") < text.index("[engine] stopped")
    assert not (tmp_path / "b").exists()


def test_write_falls_back_when_a_dir_cannot_be_created(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("a file, not a directory")
    log = CrashLog([str(blocked), str(tmp_path / "fallback")])

    log.write("hello")

    assert "hello" in (tmp_path / "fallback" / "service_crash.log").read_text()


def test_write_never_raises_when_no_dir_is_writable(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("a file, not a directory")

    CrashLog([str(blocked)]).write("dropped")


def test_write_rotates_instead_of_growing_without_bound(tmp_path):
    log = CrashLog([str(tmp_path)], max_bytes=200, backup_count=1)

    for i in range(50):
        log.write(f"line {i:03d} " + "x" * 20)

    assert (tmp_path / "service_crash.log").stat().st_size <= 200
    assert (tmp_path / "service_crash.log.1").exists()
    assert not (tmp_path / "service_crash.log.2").exists()


def test_no_module_keeps_its_own_crash_log_writer():
    src = Path(__file__).parent.parent / "src"
    offenders = [
        str(path.relative_to(src))
        for path in src.rglob("*.py")
        if path.name != "crash_log.py"
        and "service_crash.log" in path.read_text(errors="replace")
    ]

    assert offenders == []
