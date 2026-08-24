# src/main.py
import sys
import os

# Load .env (repo root, gitignored) before anything reads os.environ —
# config.py's os.environ.get() calls run at import time below.
from dotenv import load_dotenv
load_dotenv()


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
    from server.mediamtx_manager import MediamtxManager
    from server.instance_manager import InstanceManager
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


def _ensure_assets():
    """Download missing binaries (mediamtx, scrcpy) before the app needs them.

    In a frozen build assets must be pre-bundled by build.bat — skip download.
    In dev mode, run download_assets.py to fetch missing binaries.
    """
    import importlib.util, pathlib
    if hasattr(sys, '_MEIPASS'):
        return  # frozen build: assets must be in bundle
    script = pathlib.Path(__file__).parent.parent / "scripts" / "download_assets.py"
    if not script.exists():
        _log(f"[assets] download script not found: {script}")
        return
    spec = importlib.util.spec_from_file_location("download_assets", script)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        mod.main()
    except SystemExit as e:
        if e.code != 0:
            _log(f"[assets] download failed (exit {e.code}) — app may not work correctly")
    except Exception:
        import traceback as _tb
        _log(f"[assets] download error: {_tb.format_exc()[:400]}")


def main():
    # Delegate service CLI args before starting GUI
    _svc_args = {"--install", "--uninstall", "--start", "--stop", "--run-service"}
    if _svc_args & set(sys.argv):
        from service_main import main as service_cli
        service_cli()
        return

    from config import VERSION
    _log(f"[GUI] starting v{VERSION} pid={os.getpid()} user={os.environ.get('USERNAME','?')}")

    _ensure_assets()

    # Remove legacy lock-screen service if still installed from older versions
    if sys.platform == "win32":
        def _win32_setup():
            import subprocess
            subprocess.run(["sc.exe", "stop", "WindowControlService"],
                           capture_output=True, timeout=10)
            subprocess.run(["sc.exe", "delete", "WindowControlService"],
                           capture_output=True, timeout=10)
            # Allow mediamtx WHEP port through Windows Firewall (idempotent)
            from config import WHEP_PORT, WEBRTC_UDP_PORT, STUN_PORT
            for proto, port in [
                ("TCP", WHEP_PORT),
                ("TCP", 8189),
                ("UDP", 8189),
                ("UDP", WEBRTC_UDP_PORT),
                ("UDP", STUN_PORT),
            ]:
                subprocess.run([
                    "netsh", "advfirewall", "firewall", "add", "rule",
                    f"name=WindowControl-WebRTC-{proto}-{port}",
                    "dir=in", "action=allow", f"protocol={proto}",
                    f"localport={port}",
                ], capture_output=True, timeout=10)
            _log(f"[GUI] firewall rules ensured for WHEP {WHEP_PORT}, ICE TCP 8189, ICE UDP {WEBRTC_UDP_PORT}, STUN {STUN_PORT}")
        threading.Thread(target=_win32_setup, daemon=True).start()

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    state = CaptureState()
    state.set_quality(QUALITY_MAP[DEFAULT_QUALITY])
    frame_queue = FrameQueue()

    mediamtx = MediamtxManager()
    instance_manager = InstanceManager(mediamtx)

    fastapi_app = create_app(state, frame_queue, instance_manager)

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
        # proxy_headers=False is load-bearing, not a default restated:
        # uvicorn defaults it to True and trusts 127.0.0.1 as a forwarding
        # proxy, so its ProxyHeadersMiddleware would rewrite
        # request.client.host from an attacker-supplied X-Forwarded-For on any
        # request whose direct peer is loopback. The public HTTP tunnel relays
        # requests through a local httpx client (loopback from this app's
        # perspective) and does not strip x-forwarded-for -- which would make
        # app.py's localhost-only guard on /internal/ spoofable. The tunnel
        # already refuses to forward /internal/ at all; this keeps the
        # app-level guard actually meaning what it documents.
        config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=PORT,
                                log_level="warning", log_config=None,
                                proxy_headers=False)
        server = uvicorn.Server(config)

        def _serve():
            # On Windows the default Proactor event loop crashes its accept loop
            # with WinError 64 ("The specified network name is no longer
            # available") when a client socket drops mid-accept — common when a
            # PWA reconnects. The Selector loop does not have this bug. Set the
            # policy on this thread before uvicorn creates its loop.
            if sys.platform == "win32":
                import asyncio
                try:
                    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
                except Exception:
                    pass
            try:
                server.run()
            except Exception:
                import traceback as _tb
                _log(f"[GUI] server thread crashed: {_tb.format_exc()[:400]}")
                # Let the thread die so the watchdog restarts it.

        _server_thread = threading.Thread(target=_serve, daemon=True)
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
    instance_manager.stop_all()
    tray.stop()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
