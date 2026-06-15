# src/main.py
import sys
import threading
from PyQt5.QtWidgets import QApplication

from config import QUALITY_MAP
from server.stream import CaptureState
from gui.launcher import LauncherWindow
from gui.tray import TrayIcon


def main():
    # Delegate service CLI args before starting GUI
    _svc_args = {"--install", "--uninstall", "--start", "--stop", "--run-service"}
    if _svc_args & set(sys.argv):
        from service_main import main as service_cli
        service_cli()
        return

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # keep alive in tray

    # CaptureState is a thin view-only object in GUI now — service owns real state.
    # Used only for LauncherWindow UI signals.
    state = CaptureState()

    launcher = LauncherWindow(state)

    # Connect to service pipe — retry in background
    _pipe = None
    if sys.platform == "win32":
        from service.pipe_client import PipeClient

        def _on_service_event(ev: dict):
            event = ev.get("event")
            if event == "lock":
                launcher.on_service_lock()
            elif event == "unlock":
                launcher.on_service_unlock()
                # Refresh window list after unlock — apps may have changed
                threading.Thread(target=_push_windows, args=(lambda cmd: _pipe.send(cmd),), daemon=True).start()

        _pipe = PipeClient(on_event=_on_service_event)
        threading.Thread(target=_try_connect_pipe, args=(_pipe, lambda cmd: _pipe.send(cmd)), daemon=True).start()

    def _send(cmd: dict):
        if _pipe and _pipe.is_connected:
            return _pipe.send(cmd)
        return None

    def start_server():
        _send({"cmd": "start"})

    def stop_server():
        _send({"cmd": "stop"})

    def show_launcher():
        launcher.show()
        launcher.raise_()
        launcher.activateWindow()

    tray = TrayIcon(
        on_show=show_launcher,
        on_stop_server=stop_server,
        on_exit=lambda: (stop_server(), app.quit()),
    )

    launcher.server_start_requested.connect(start_server)
    launcher.server_stop_requested.connect(stop_server)
    launcher.quality_changed.connect(lambda q: _send({"cmd": "quality", "value": q}))
    launcher.window_selected.connect(lambda hwnd, title: _send({"cmd": "select", "hwnd": hwnd}))

    launcher.show()
    tray.start()

    exit_code = app.exec_()
    stop_server()
    tray.stop()
    sys.exit(exit_code)


def _try_connect_pipe(pipe, send_fn):
    import time
    while True:
        if pipe.connect():
            _push_windows(send_fn)
            # Re-push every 30s so service stays current
            while pipe.is_connected:
                time.sleep(30)
                _push_windows(send_fn)
            return
        time.sleep(3)


def _push_windows(send_fn):
    try:
        from server.window_manager import list_windows
        windows = [{"id": w.hwnd, "title": w.title} for w in list_windows()]
        send_fn({"cmd": "push_windows", "list": windows})
    except Exception:
        pass


if __name__ == "__main__":
    main()
