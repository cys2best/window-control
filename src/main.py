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

        def _on_pipe_reconnect():
            # Re-sync desktop state after pipe reconnects (may have missed events)
            import time
            time.sleep(0.5)
            state.set_desktop("Default")
            available_windows.clear()
            available_windows.extend(_build_available_windows())
            launcher.on_service_unlock()

        _pipe = PipeClient(on_event=_on_service_event)
        threading.Thread(target=_try_connect_pipe, args=(_pipe, _on_pipe_reconnect), daemon=True).start()

    launcher = LauncherWindow(state)

    def show_launcher():
        launcher.show()
        launcher.raise_()
        launcher.activateWindow()

    def _force_reinstall():
        def _run():
            from updater import _fetch_latest_version, download_and_install
            _log("[Reinstall] Fetching latest version…")
            tray.notify("Fetching latest release…", "WindowControl Update")
            latest = _fetch_latest_version()
            if not latest:
                _log("[Reinstall] Failed to fetch latest version from GitHub")
                tray.notify("Could not fetch latest release. Check internet.", "Update Failed")
                return
            _log(f"[Reinstall] Downloading v{latest}…")
            tray.notify(f"Downloading v{latest}…", "WindowControl Update")

            def _on_error(msg):
                _log(f"[Reinstall] Download failed: {msg}")
                tray.notify(f"Download failed: {msg}", "Update Failed")

            download_and_install(latest, on_error=_on_error)

        threading.Thread(target=_run, daemon=True).start()

    tray = TrayIcon(
        on_show=show_launcher,
        on_stop_server=stop_server,
        on_exit=lambda: (stop_server(), app.quit()),
        on_reinstall=_force_reinstall,
    )

    launcher.server_start_requested.connect(start_server)
    launcher.server_stop_requested.connect(stop_server)
    launcher.quality_changed.connect(state.set_quality)
    launcher.window_selected.connect(lambda hwnd, title: state.set_hwnd(hwnd))

    # Prevent Windows from locking session due to inactivity
    threading.Thread(target=_keep_session_alive, daemon=True).start()

    launcher.show()
    tray.start()

    exit_code = app.exec_()
    _log(f"[GUI] app.exec_() returned exit_code={exit_code} — process exiting")
    stop_server()
    tray.stop()
    sys.exit(exit_code)


def _keep_session_alive():
    """Prevent Windows inactivity lock by resetting execution state periodically.

    ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED | ES_CONTINUOUS tells Windows
    this app needs the session active — same mechanism used by media players.
    Also calls SetThreadExecutionState every 30s to reset inactivity timer.
    """
    if sys.platform != "win32":
        return
    import ctypes
    ES_CONTINUOUS       = 0x80000000
    ES_SYSTEM_REQUIRED  = 0x00000001
    ES_DISPLAY_REQUIRED = 0x00000002
    flags = ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
    ctypes.windll.kernel32.SetThreadExecutionState(flags)
    import time
    while True:
        time.sleep(30)
        ctypes.windll.kernel32.SetThreadExecutionState(flags)


def _try_connect_pipe(pipe, on_reconnect=None):
    import time
    while True:
        if pipe.connect():
            if on_reconnect:
                on_reconnect()
            # Wait until disconnected, then retry
            while pipe.is_connected:
                time.sleep(1)
        time.sleep(3)


if __name__ == "__main__":
    main()
