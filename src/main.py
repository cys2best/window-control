# src/main.py
import sys
from PyQt5.QtWidgets import QApplication

from config import QUALITY_MAP, DEFAULT_QUALITY
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

    state = CaptureState()
    state.set_quality(QUALITY_MAP[DEFAULT_QUALITY])

    # GUI communicates with service via named pipe — no local uvicorn
    from service.pipe_client import PipeClient
    pipe = PipeClient()
    pipe.connect()

    def start_server():
        if not pipe.is_connected:
            pipe.connect()
        pipe.send({"cmd": "start"})

    def stop_server():
        if pipe.is_connected:
            pipe.send({"cmd": "stop"})

    launcher = LauncherWindow(state)

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
    launcher.quality_changed.connect(
        lambda q: pipe.send({"cmd": "quality", "value": q}) if pipe.is_connected else None
    )
    launcher.window_selected.connect(
        lambda hwnd, title: pipe.send({"cmd": "select", "id": hwnd}) if pipe.is_connected else None
    )

    launcher.show()
    tray.start()

    exit_code = app.exec_()
    stop_server()
    pipe.disconnect()
    tray.stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
