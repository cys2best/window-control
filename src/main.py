# src/main.py
import sys
import threading
import uvicorn
from PyQt5.QtWidgets import QApplication

from config import PORT, QUALITY_MAP, DEFAULT_QUALITY
from server.app import create_app
from server.stream import CaptureState, FrameQueue, capture_loop
from server.window_manager import list_windows
from gui.launcher import LauncherWindow
from gui.tray import TrayIcon


def _log(msg: str):
    import os
    for _p in [r"C:\ProgramData\WindowControl", r"C:\Windows\Temp"]:
        try:
            os.makedirs(_p, exist_ok=True)
            with open(os.path.join(_p, "service_crash.log"), "a") as f:
                f.write(msg + "\n")
            return
        except Exception:
            continue


def _build_available_windows():
    windows = list_windows()
    return [{"id": w.hwnd, "title": w.title} for w in windows]


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
    frame_queue = FrameQueue()

    available_windows = _build_available_windows()
    fastapi_app = create_app(state, frame_queue, available_windows)

    config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=PORT,
                            log_level="warning", log_config=None)
    server = uvicorn.Server(config)
    _server_thread = None
    _capture_thread = None

    def start_server():
        nonlocal _server_thread, _capture_thread
        state.running = True
        available_windows.clear()
        available_windows.extend(_build_available_windows())
        _capture_thread = threading.Thread(
            target=capture_loop, args=(state, frame_queue), daemon=True
        )
        _capture_thread.start()
        _server_thread = threading.Thread(target=server.run, daemon=True)
        _server_thread.start()

    def stop_server():
        state.running = False
        server.should_exit = True

    # Connect to service for lock/unlock desktop switching
    _pipe = None
    if sys.platform == "win32":
        from service.pipe_client import PipeClient

        def _on_service_event(ev: dict):
            event = ev.get("event")
            if event == "lock":
                state.set_desktop("Winlogon")
                launcher.on_service_lock()
            elif event == "unlock":
                state.set_desktop("Default")
                available_windows.clear()
                available_windows.extend(_build_available_windows())
                launcher.on_service_unlock()

        _pipe = PipeClient(on_event=_on_service_event)
        threading.Thread(target=_try_connect_pipe, args=(_pipe,), daemon=True).start()

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
    launcher.quality_changed.connect(state.set_quality)
    launcher.window_selected.connect(lambda hwnd, title: state.set_hwnd(hwnd))

    launcher.show()
    tray.start()

    exit_code = app.exec_()
    _log(f"[GUI] app.exec_() returned exit_code={exit_code} — process exiting")
    stop_server()
    tray.stop()
    sys.exit(exit_code)


def _try_connect_pipe(pipe):
    import time
    while True:
        if pipe.connect():
            return
        time.sleep(3)


if __name__ == "__main__":
    main()
