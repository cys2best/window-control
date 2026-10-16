# src/main.py
import sys
import os
import secrets
import logging

# Load .env (repo root, gitignored) before anything reads os.environ —
# config.py's os.environ.get() calls run at import time below.
from dotenv import load_dotenv
load_dotenv()

# Without this, the root logger defaults to WARNING and every module's
# log.info() (engine lifecycle, pairing, etc.) is silently
# dropped even when stdout/stderr are captured to a file.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

import config
import crash_log

# apps/desktop/tray.py lives outside src/ (a sibling app directory,
# like apps/web and apps/mobile), so unlike gui.launcher it is
# not naturally importable via src/ being main.py's own directory. In a dev
# checkout, add apps/desktop to sys.path so `import tray` resolves the same
# way `import gui.launcher` already does for modules that stayed under src/.
# In a PyInstaller-frozen build this is unnecessary and the directory won't
# exist at this relative path -- those modules are instead pulled into the
# frozen bundle via window_control.spec's `pathex`, which PyInstaller's own
# import graph resolves without any runtime sys.path change.
if not hasattr(sys, "_MEIPASS"):
    import pathlib
    _desktop_dir = str(pathlib.Path(__file__).resolve().parent.parent / "apps" / "desktop")
    if _desktop_dir not in sys.path:
        sys.path.insert(0, _desktop_dir)


_log = crash_log.log


_log(f"[gui-imports-start] pid={os.getpid()} user={os.environ.get('USERNAME','?')}")

try:
    import threading
    import uvicorn
    _log("[gui-imports] threading+uvicorn OK")
except Exception:
    import traceback as _tb
    _log(f"[gui-imports] threading/uvicorn FAILED: {_tb.format_exc()[:400]}")
    raise

try:
    from PyQt5.QtWidgets import QApplication
    _log("[gui-imports] PyQt5 OK")
except Exception:
    import traceback as _tb
    _log(f"[gui-imports] PyQt5 FAILED: {_tb.format_exc()[:400]}")
    raise

try:
    from config import PORT
    from server.app import create_app
    from server.instance_manager import InstanceManager
    from gui.launcher import LauncherWindow
    from server.pairing import PairingStore, default_store_path
    from tray import TrayIcon
    _log("[gui-imports] app modules OK")
except Exception:
    import traceback as _tb
    _log(f"[gui-imports] app modules FAILED: {_tb.format_exc()[:600]}")
    raise


def _ensure_assets():
    """Download missing scrcpy binaries before the app needs them.

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


def build_engine_orchestrator() -> "EngineOrchestrator":
    exe_path = config.engine_exe_path()
    if not os.path.isfile(exe_path):
        raise RuntimeError(f"engine.exe not found at {exe_path}")

    from server.engine_orchestrator import EngineOrchestrator
    from server.engine_runtime import EngineRuntimeConfig

    runtime_config = EngineRuntimeConfig(
        exe_path=exe_path,
        whep_secret=secrets.token_hex(32),
        local_ice_servers=config.ENGINE_LOCAL_ICE_SERVERS,
    )
    return EngineOrchestrator(runtime_config)


def create_identity_store(service_url):
    """One identity store shared by the host client and launcher license actions."""
    from server.remote_identity import RemoteIdentityStore, RemoteIdentityError
    if not service_url:
        return None
    try:
        return RemoteIdentityStore()
    except RemoteIdentityError:
        return None


class LicenseActions:
    """Launcher license calls; results also update the live client's status."""

    def __init__(self, store, client):
        self._store = store
        self._client = client

    def _record(self, licensed):
        client = self._client()
        if client is not None:
            client.relay_licensed = licensed
        return licensed

    def activate(self, key):
        return self._record(self._store.activate_license(key))

    def deactivate(self):
        return self._record(self._store.deactivate_license())


def forward_remote_state(launcher, client, remote_pairing, error):
    """Called from the server loop; launcher signals hop to the Qt thread."""
    if launcher is None:
        return
    launcher.remote_pairing_changed.emit(remote_pairing, error)
    if client is not None:
        launcher.relay_licensed_changed.emit(client.relay_licensed)


async def build_remote_client(service_url, actions, pairing, on_state, *, identity_store=None):
    """Called on the server loop; registration/protection run in its executor."""
    import asyncio
    from server.remote_identity import RemoteIdentityStore, RemoteIdentityError
    from server.remote_dispatch import RemoteDispatcher
    from server.remote_client import RemoteHostClient
    from server.remote_sessions import RemoteSessionCoordinator
    from server.engine_remote import EngineRemoteClient
    if not service_url:
        on_state(None, "Remote service is not configured")
        return None
    try:
        store = identity_store if identity_store is not None else RemoteIdentityStore()
        identity = await asyncio.to_thread(store.load_or_register, service_url)
        dispatcher = RemoteDispatcher(actions, pairing)
        host = RemoteHostClient(service_url, identity, dispatcher=dispatcher, on_state=on_state,
                                identity_store=store)
        sessions = RemoteSessionCoordinator(identity.installation_id, actions, pairing,
                                            actions.manager._engine_orchestrator.remote_endpoint,
                                            EngineRemoteClient(), authority=host)
        dispatcher.sessions = sessions
        return host
    except RemoteIdentityError as exc:
        on_state(None, str(exc))
    except Exception:
        on_state(None, "Remote identity is unavailable")
    return None


def _remove_legacy_services():
    """Stop and delete legacy Windows services and purge stored unlock credentials."""
    if sys.platform == "win32":
        import subprocess
        for svc in ("EmuCtrlService", "WindowControlService"):
            subprocess.run(["sc.exe", "stop", svc], capture_output=True, timeout=10)
            subprocess.run(["sc.exe", "delete", svc], capture_output=True, timeout=10)
        for dat in (r"C:\ProgramData\EmuCtrl\unlock.dat", r"C:\ProgramData\WindowControl\unlock.dat"):
            try:
                if os.path.exists(dat):
                    os.remove(dat)
            except Exception:
                pass


def main():
    # Handle legacy service CLI args gracefully without starting GUI
    _svc_args = {"--install", "--uninstall", "--start", "--stop", "--run-service"}
    if _svc_args & set(sys.argv):
        if "--uninstall" in sys.argv or "--stop" in sys.argv:
            _remove_legacy_services()
            print("Legacy EmuCtrl/WindowControl services and stored unlock data removed.")
            return
        print("EmuCtrl lock screen service has been deprecated and removed.")
        return

    from config import VERSION
    _log(f"[GUI] starting v{VERSION} pid={os.getpid()} user={os.environ.get('USERNAME','?')}")

    _ensure_assets()

    # Remove legacy lock-screen service if still installed from older versions
    if sys.platform == "win32":
        def _win32_setup():
            import subprocess
            _remove_legacy_services()
            # Keep the embedded STUN binding reachable on the LAN/Tailscale
            # interface. Engine program rules are installed with the package.
            from config import STUN_PORT
            subprocess.run([
                "netsh", "advfirewall", "firewall", "add", "rule",
                f"name=EmuCtrl-STUN-UDP-{STUN_PORT}",
                "dir=in", "action=allow", "protocol=UDP",
                f"localport={STUN_PORT}",
            ], capture_output=True, timeout=10)
            _log(f"[GUI] firewall rule ensured for STUN {STUN_PORT}")
        threading.Thread(target=_win32_setup, daemon=True).start()

    # Must be set before the application exists. Without them Qt 5 on a
    # scaled Windows display grows point-size text but not pixel sizes, and
    # the fixed-size host window clips its own content.
    from PyQt5.QtCore import Qt
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    engine_orchestrator = build_engine_orchestrator()
    instance_manager = InstanceManager(engine_orchestrator)

    pairing = PairingStore(default_store_path())
    launcher = None
    remote_client = None
    identity_store = create_identity_store(config.REMOTE_SERVICE_URL)

    def remote_state(remote_pairing, error):
        forward_remote_state(launcher, remote_client, remote_pairing, error)

    async def remote_factory(actions):
        nonlocal remote_client
        remote_client = await build_remote_client(config.REMOTE_SERVICE_URL, actions, pairing, remote_state,
                                                  identity_store=identity_store)
        return remote_client

    fastapi_app = create_app(instance_manager, pairing=pairing, remote_factory=remote_factory)

    server = None
    _server_thread = None

    def start_server():
        nonlocal _server_thread, server
        # Fresh uvicorn Server each restart (uvicorn cannot be re-run after exit)
        # proxy_headers=False is load-bearing: uvicorn's default trusts
        # X-Forwarded-For from loopback peers and rewrites the client
        # address, which is the address the access gate checks.
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

    launcher = LauncherWindow(on_stop_server=stop_server, pairing=pairing, license_actions=(
        LicenseActions(identity_store, lambda: remote_client) if identity_store is not None else None))

    def show_launcher():
        launcher.show()
        launcher.raise_()
        launcher.activateWindow()

    def _force_reinstall():
        def _run():
            from updater import _fetch_latest_version, download_and_install
            _log("[Reinstall] Fetching latest version…")
            tray.notify("Fetching latest release…", "EmuCtrl Update")
            latest = _fetch_latest_version()
            if not latest:
                _log("[Reinstall] Failed to fetch latest version from GitHub")
                tray.notify("Could not fetch latest release. Check internet.", "Update Failed")
                return
            _log(f"[Reinstall] Downloading v{latest}…")
            tray.notify(f"Downloading v{latest}…", "EmuCtrl Update")

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
