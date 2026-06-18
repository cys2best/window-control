# src/main.py
import sys
import os


def _log_early(msg: str):
    for _p in [r"C:\ProgramData\WindowControl", r"C:\Windows\Temp", r"C:\Temp"]:
        try:
            os.makedirs(_p, exist_ok=True)
            with open(os.path.join(_p, "service_crash.log"), "a") as _f:
                _f.write(msg + "\n")
            return
        except Exception:
            continue


_log_early(f"[gui-imports-start] pid={os.getpid()} user={os.environ.get('USERNAME','?')}")

try:
    import threading
    import uvicorn
    _log_early("[gui-imports] threading+uvicorn OK")
except Exception:
    import traceback as _tb
    _log_early(f"[gui-imports] threading/uvicorn FAILED: {_tb.format_exc()[:400]}")
    raise

try:
    from PyQt5.QtWidgets import QApplication
    _log_early("[gui-imports] PyQt5 OK")
except Exception:
    import traceback as _tb
    _log_early(f"[gui-imports] PyQt5 FAILED: {_tb.format_exc()[:400]}")
    raise

try:
    from config import PORT, QUALITY_MAP, DEFAULT_QUALITY
    from server.app import create_app
    from server.stream import CaptureState, FrameQueue, capture_loop
    from gui.launcher import LauncherWindow
    from gui.tray import TrayIcon
    _log_early("[gui-imports] app modules OK")
except Exception:
    import traceback as _tb
    _log_early(f"[gui-imports] app modules FAILED: {_tb.format_exc()[:600]}")
    raise


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


def main():
    # Delegate service CLI args before starting GUI
    _svc_args = {"--install", "--uninstall", "--start", "--stop", "--run-service"}
    if _svc_args & set(sys.argv):
        from service_main import main as service_cli
        service_cli()
        return

    from config import VERSION
    _log(f"[GUI] starting v{VERSION} pid={os.getpid()} user={os.environ.get('USERNAME','?')}")

    # Remove legacy lock-screen service if still installed from older versions
    if sys.platform == "win32":
        def _remove_legacy_service():
            import subprocess
            subprocess.run(["sc.exe", "stop", "WindowControlService"],
                           capture_output=True, timeout=10)
            subprocess.run(["sc.exe", "delete", "WindowControlService"],
                           capture_output=True, timeout=10)
        threading.Thread(target=_remove_legacy_service, daemon=True).start()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    state = CaptureState()
    state.set_quality(QUALITY_MAP[DEFAULT_QUALITY])
    frame_queue = FrameQueue()

    fastapi_app = create_app(state, frame_queue)

    server = None
    _server_thread = None
    _capture_thread = None

    def start_server():
        nonlocal _server_thread, _capture_thread, server
        state.running = True
        _capture_thread = threading.Thread(
            target=capture_loop, args=(state, frame_queue), daemon=True
        )
        _capture_thread.start()
        # Fresh uvicorn Server each restart (uvicorn cannot be re-run after exit)
        config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=PORT,
                                log_level="warning", log_config=None)
        server = uvicorn.Server(config)
        _server_thread = threading.Thread(target=server.run, daemon=True)
        _server_thread.start()
        _log("[GUI] server started")

    def stop_server():
        state.running = False
        if server:
            server.should_exit = True

    def _watchdog():
        import time
        while True:
            time.sleep(10)
            if _server_thread and not _server_thread.is_alive():
                _log("[GUI] watchdog: server thread dead — restarting")
                try:
                    start_server()
                except Exception:
                    import traceback as _tb
                    _log(f"[GUI] watchdog restart failed: {_tb.format_exc()[:300]}")
    threading.Thread(target=_watchdog, daemon=True).start()

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

    launcher.quality_changed.connect(state.set_quality)

    launcher.show()
    tray.start()
    start_server()

    exit_code = app.exec_()
    _log(f"[GUI] app.exec_() returned exit_code={exit_code} — process exiting")
    stop_server()
    tray.stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
