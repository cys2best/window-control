import re

from broker.admin import main
from broker.license_store import LicenseStore, normalize_key

A = "a" * 32


def test_issue_prints_key_once_and_list_never_shows_it(tmp_path, capsys):
    path = tmp_path / "licenses.json"
    assert main(["--path", str(path), "issue", "--days", "30", "--note", "friend pc"], now=lambda: 0) == 0
    key = capsys.readouterr().out.strip()
    assert re.fullmatch(r"EMU(-[0-9A-HJKMNP-TV-Z]{5}){4}", key)
    LicenseStore(path).activate(key, A, now=10, installation_exists=lambda _id: True)
    assert main(["--path", str(path), "list"], now=lambda: 20) == 0
    out = capsys.readouterr().out
    assert key not in out and normalize_key(key) not in out
    line, = out.strip().splitlines()
    assert line.split()[1] == A
    assert "issued=1970-01-01T00:00:00Z" in line and "bound=1970-01-01T00:00:10Z" in line
    assert "moved=-" in line and "expires=1970-01-31T00:00:00Z" in line
    assert line.endswith("active friend pc")


def test_reset_and_revoke_unknown_key_exit_1(tmp_path, capsys):
    path = str(tmp_path / "licenses.json")
    for command in ("reset", "revoke"):
        assert main(["--path", path, command, "EMU-00000-00000-00000-00000"]) == 1
        captured = capsys.readouterr()
        assert captured.err.strip() == "unknown key" and captured.out == ""
    main(["--path", path, "issue"])
    key = capsys.readouterr().out.strip()
    assert main(["--path", path, "reset", key.lower()]) == 0
    assert capsys.readouterr().out.strip() == "ok"


def test_revoke_visible_to_running_store(tmp_path, capsys, monkeypatch):
    path = tmp_path / "licenses.json"
    monkeypatch.setenv("REMOTE_LICENSE_PATH", str(path))
    running = LicenseStore(path)
    assert main(["issue"]) == 0
    key = capsys.readouterr().out.strip()
    running.activate(key, A, now=0, installation_exists=lambda _id: True)
    assert running.is_licensed(A, now=1)
    assert main(["revoke", key]) == 0
    assert capsys.readouterr().out.strip() == "ok"
    assert running.is_licensed(A, now=2) is False
    assert main(["list"]) == 0
    assert capsys.readouterr().out.split()[-1] == "revoked"
