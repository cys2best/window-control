# apps/desktop/test_window.py
import json
import os
import subprocess
import sys
import threading

import pytest

from window import WEBVIEW_ARG, DesktopWindow, _entry_script

HERE = os.path.dirname(os.path.abspath(__file__))


class _FakeProcess:
    def __init__(self):
        self.exit_code = None
        self.terminated = False

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.terminated = True


class _RecordingPopen:
    def __init__(self):
        self.calls = []
        self.processes = []

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        process = _FakeProcess()
        self.processes.append(process)
        return process


def test_show_launches_the_webview_entry_point_in_its_own_process():
    popen = _RecordingPopen()
    DesktopWindow("http://127.0.0.1:8000", popen=popen).show()

    assert len(popen.calls) == 1
    command, _ = popen.calls[0]
    assert command == [sys.executable, _entry_script(), "http://127.0.0.1:8000"]
    assert os.path.isfile(_entry_script())


def test_show_does_not_open_a_second_window_while_one_is_alive():
    popen = _RecordingPopen()
    window = DesktopWindow("http://127.0.0.1:8000", popen=popen)
    window.show()
    window.show()
    assert len(popen.calls) == 1


def test_show_opens_a_fresh_window_after_the_previous_one_was_closed():
    popen = _RecordingPopen()
    window = DesktopWindow("http://127.0.0.1:8000", popen=popen)
    window.show()
    popen.processes[0].exit_code = 0  # user closed the window
    window.show()
    assert len(popen.calls) == 2


def test_close_terminates_the_running_window_process():
    popen = _RecordingPopen()
    window = DesktopWindow("http://127.0.0.1:8000", popen=popen)
    window.show()
    window.close()
    assert popen.processes[0].terminated is True
    # Idempotent: closing twice must not explode.
    window.close()


def test_frozen_build_reinvokes_the_app_executable_with_the_webview_flag(monkeypatch):
    # In a PyInstaller build sys.executable is WindowControl.exe, not a
    # Python interpreter, so there is no .py path to hand it.
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    popen = _RecordingPopen()
    DesktopWindow("http://127.0.0.1:8000", popen=popen).show()
    command, _ = popen.calls[0]
    assert command == [sys.executable, WEBVIEW_ARG, "http://127.0.0.1:8000"]


def test_show_never_starts_pywebview_inside_this_process():
    # The regression this whole design exists for: webview.start() run
    # from anywhere but a process main thread raises, so DesktopWindow
    # must not touch pywebview in-process at all.
    import window as window_module

    assert not hasattr(window_module, "webview")
    assert "import webview" not in open(window_module.__file__).read()


def test_real_pywebview_refuses_to_start_off_the_main_thread():
    # Pins the actual library constraint that made the previous
    # background-thread implementation silently dead on arrival. If a
    # future pywebview drops this check, this test tells us the
    # subprocess indirection could be revisited.
    webview = pytest.importorskip("webview")
    box = {}

    def _run():
        try:
            webview.start()
        except BaseException as exc:  # noqa: BLE001 - recording it is the point
            box["exc"] = exc

    thread = threading.Thread(target=_run)
    thread.start()
    thread.join(30)
    assert "main thread" in str(box.get("exc", "")).lower()


_PROBE = r"""
import json, sys, threading, types

recorded = {}
stub = types.ModuleType("webview")


def create_window(title, url, width=None, height=None):
    recorded["window"] = [title, url, width, height]
    return object()


def start(*args, **kwargs):
    recorded["start_thread"] = threading.current_thread().name


stub.create_window = create_window
stub.start = start
sys.modules["webview"] = stub

sys.path.insert(0, sys.argv[1])
import webview_main

recorded["rc"] = webview_main.main([sys.argv[2]])
print(json.dumps(recorded))
"""


def test_webview_entry_point_calls_start_on_a_real_process_main_thread():
    # Runs webview_main's real code in a real child process (with only
    # pywebview itself stubbed, so no GUI opens) and checks the thread
    # identity pywebview would check. This is the test that would have
    # failed against the old design, where start() ran on "Thread-N".
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, HERE, "http://127.0.0.1:8000"],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stderr
    recorded = json.loads(result.stdout.strip().splitlines()[-1])
    assert recorded["start_thread"] == "MainThread"
    assert recorded["window"] == ["WindowControl", "http://127.0.0.1:8000", 1100, 750]
    assert recorded["rc"] == 0


def test_webview_entry_point_rejects_a_missing_url():
    import webview_main

    assert webview_main.main([]) == 2
