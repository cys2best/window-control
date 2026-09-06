from pathlib import Path
import sys
import pytest


def test_build_engine_orchestrator_rejects_missing_engine(monkeypatch):
    import main as main_mod

    monkeypatch.setattr(
        main_mod.config, "engine_exe_path", lambda: "C:/missing/engine.exe"
    )
    monkeypatch.setattr(main_mod.os.path, "isfile", lambda _: False)

    with pytest.raises(RuntimeError, match="engine.exe"):
        main_mod.build_engine_orchestrator()


def test_build_engine_orchestrator_uses_fresh_generated_launch_secret(monkeypatch):
    import main as main_mod

    token_sizes = []
    tokens = iter(["a" * 64, "b" * 64])
    monkeypatch.setattr(main_mod.config, "engine_exe_path", lambda: "C:/app/engine.exe")
    monkeypatch.setattr(main_mod.os.path, "isfile", lambda _: True)
    monkeypatch.setattr(
        main_mod.secrets,
        "token_hex",
        lambda size: token_sizes.append(size) or next(tokens),
    )

    first = main_mod.build_engine_orchestrator()
    second = main_mod.build_engine_orchestrator()

    assert first.config.whep_secret == "a" * 64
    assert second.config.whep_secret == "b" * 64
    assert token_sizes == [32, 32]


def test_build_engine_orchestrator_passes_configured_runtime_values(monkeypatch):
    import main as main_mod
    from server import install_identity
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    generated_key = Ed25519PrivateKey.generate()

    monkeypatch.setattr(main_mod.config, "engine_exe_path", lambda: "C:/app/engine.exe")
    monkeypatch.setattr(main_mod.os.path, "isfile", lambda _: True)
    monkeypatch.setattr(main_mod.secrets, "token_hex", lambda size: "generated-whep")
    monkeypatch.setattr(main_mod.config, "VPS_SIGNALING_URL", "wss://signal.example/ws")
    monkeypatch.setattr(main_mod.config, "SUPABASE_URL", "https://project.supabase.co", raising=False)
    monkeypatch.setattr(main_mod.config, "ENGINE_LOCAL_ICE_SERVERS", ("stun:local", "turn:local"), raising=False)
    monkeypatch.setattr(main_mod.config, "ENGINE_PUBLIC_ICE_SERVERS", ("stun:public", "turn:public"), raising=False)
    monkeypatch.setattr(
        install_identity,
        "get_or_create_install_keypair",
        lambda: (generated_key, "pubkey-b64"),
    )

    orchestrator = main_mod.build_engine_orchestrator()

    assert orchestrator.config.exe_path == "C:/app/engine.exe"
    assert orchestrator.config.whep_secret == "generated-whep"
    assert orchestrator.config.signaling_url == "wss://signal.example/ws"
    assert orchestrator.config.signaling_private_key is generated_key
    assert orchestrator.config.local_ice_servers == ("stun:local", "turn:local")
    assert orchestrator.config.public_ice_servers == ("stun:public", "turn:public")


def test_build_engine_orchestrator_disables_signaling_without_a_supabase_project(monkeypatch):
    """VPS_SIGNALING_URL alone isn't enough to enable the public path -- with
    no SUPABASE_URL there is no account to route the session by, so signaling
    stays off entirely rather than starting the engine with a dead relay URL.
    """
    import main as main_mod
    from server import install_identity

    keypair_calls = []

    monkeypatch.setattr(main_mod.config, "engine_exe_path", lambda: "C:/app/engine.exe")
    monkeypatch.setattr(main_mod.os.path, "isfile", lambda _: True)
    monkeypatch.setattr(main_mod.secrets, "token_hex", lambda size: "generated-whep")
    monkeypatch.setattr(main_mod.config, "VPS_SIGNALING_URL", "wss://signal.example/ws")
    monkeypatch.setattr(main_mod.config, "SUPABASE_URL", None, raising=False)
    monkeypatch.setattr(main_mod.config, "ENGINE_LOCAL_ICE_SERVERS", (), raising=False)
    monkeypatch.setattr(main_mod.config, "ENGINE_PUBLIC_ICE_SERVERS", (), raising=False)
    monkeypatch.setattr(
        install_identity,
        "get_or_create_install_keypair",
        lambda: keypair_calls.append(1),
    )

    orchestrator = main_mod.build_engine_orchestrator()

    assert orchestrator.config.signaling_url == ""
    assert orchestrator.config.signaling_private_key is None
    assert keypair_calls == []


class _FakeThread:
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass


class _FakeApplication:
    def __init__(self, argv):
        self.argv = argv

    def setQuitOnLastWindowClosed(self, value):
        pass

    def exec_(self):
        return 0

    def quit(self):
        pass


class _FakeLauncher:
    def __init__(self, *args, **kwargs):
        pass

    def show(self):
        pass

    def raise_(self):
        pass

    def activateWindow(self):
        pass


class _FakeTray:
    def __init__(self, **kwargs):
        pass

    def start(self):
        pass

    def stop(self):
        pass


def _patch_main_startup(monkeypatch, main_mod, manager_calls):
    monkeypatch.setattr(main_mod, "QApplication", _FakeApplication)
    monkeypatch.setattr(main_mod, "LauncherWindow", _FakeLauncher)
    monkeypatch.setattr(main_mod, "TrayIcon", _FakeTray)
    monkeypatch.setattr(main_mod, "_ensure_assets", lambda: None)
    monkeypatch.setattr(main_mod.threading, "Thread", _FakeThread)

    class FakeManager:
        def __init__(self, *args, **kwargs):
            manager_calls.append((args, kwargs))

        def stop_all(self):
            pass

    monkeypatch.setattr(main_mod, "InstanceManager", FakeManager)
    monkeypatch.setattr(main_mod, "create_app", lambda *args: object())


def test_main_constructs_only_engine_instance_manager(monkeypatch):
    import main as main_mod

    manager_calls = []
    build_calls = []
    orchestrator = object()
    _patch_main_startup(monkeypatch, main_mod, manager_calls)
    monkeypatch.setattr(
        main_mod,
        "build_engine_orchestrator",
        lambda: build_calls.append(None) or orchestrator,
    )
    monkeypatch.setattr(main_mod.sys, "argv", ["main.py"])

    with pytest.raises(SystemExit) as exit_info:
        main_mod.main()

    assert exit_info.value.code == 0
    assert build_calls == [None]
    assert manager_calls == [((orchestrator,), {})]


def test_main_starts_server_without_android_mjpeg_pipeline(monkeypatch):
    import main as main_mod

    manager_calls = []
    app_calls = []
    thread_targets = []

    class RecordingThread:
        def __init__(self, *args, target=None, **kwargs):
            thread_targets.append(target)

        def start(self):
            pass

    _patch_main_startup(monkeypatch, main_mod, manager_calls)
    monkeypatch.setattr(main_mod.threading, "Thread", RecordingThread)
    monkeypatch.setattr(
        main_mod, "create_app", lambda *args, **kwargs: app_calls.append((args, kwargs)) or object()
    )
    monkeypatch.setattr(main_mod, "build_engine_orchestrator", object)
    monkeypatch.setattr(main_mod.sys, "argv", ["main.py"])

    with pytest.raises(SystemExit) as exit_info:
        main_mod.main()

    assert exit_info.value.code == 0
    assert len(app_calls) == 1
    assert len(app_calls[0][0]) == 1
    assert all(getattr(target, "__name__", "") != "capture_loop" for target in thread_targets)


def test_main_does_not_run_pywebview_on_a_background_thread():
    # Regression guard: webview.start() raises WebViewException unless it
    # is on the process main thread, so main.py must never hand it to a
    # threading.Thread (it did once, silently opening no window at all).
    source = (Path(__file__).parent.parent / "src" / "main.py").read_text()
    assert "desktop_window.start" not in source
    assert "webview.start" not in source


def test_config_imports():
    from config import PORT, VERSION, SCRCPY_PATH, WEB_BUILD_DIR, ASSETS_DIR, engine_exe_path
    assert PORT == 8080
    assert callable(engine_exe_path)


def test_instance_name_emulator():
    from server.instance_manager import instance_name
    assert instance_name("emulator-5554") == "instance0"
    assert instance_name("emulator-5556") == "instance1"
    assert instance_name("emulator-5558") == "instance2"


def test_instance_name_non_emulator():
    from server.instance_manager import instance_name
    name = instance_name("192.168.1.100:5555")
    assert name.startswith("instance_")


def test_main_retains_only_stun_firewall_rule_no_legacy_whep_ice_loop():
    source = (Path(__file__).parent.parent / "src" / "main.py").read_text()
    assert "STUN" in source
    assert "netsh" in source
    assert "WindowControl-Engine" not in source
    for legacy in ("WHEP", "ICE_PORT", "for port in", "ice_port_range"):
        assert legacy not in source


def test_main_handles_deprecated_service_args(monkeypatch, capsys):
    import main as main_mod

    monkeypatch.setattr(main_mod.sys, "argv", ["main.py", "--install"])
    main_mod.main()
    captured = capsys.readouterr()
    assert "deprecated and removed" in captured.out


def test_main_handles_service_uninstall_arg(monkeypatch, capsys):
    import main as main_mod

    cleaned = []
    monkeypatch.setattr(main_mod, "_remove_legacy_services", lambda: cleaned.append(True))
    monkeypatch.setattr(main_mod.sys, "argv", ["main.py", "--uninstall"])
    main_mod.main()
    captured = capsys.readouterr()
    assert cleaned == [True]
    assert "removed" in captured.out


def test_remove_legacy_services_invokes_sc_on_win32(monkeypatch):
    import main as main_mod

    calls = []
    removed_files = []
    monkeypatch.setattr(main_mod.sys, "platform", "win32")
    monkeypatch.setattr(
        "subprocess.run",
        lambda cmd, **kwargs: calls.append(cmd) or object(),
    )
    monkeypatch.setattr("os.path.exists", lambda path: True)
    monkeypatch.setattr("os.remove", lambda path: removed_files.append(path))

    main_mod._remove_legacy_services()

    assert ["sc.exe", "stop", "EmuCtrlService"] in calls
    assert ["sc.exe", "delete", "EmuCtrlService"] in calls
    assert ["sc.exe", "stop", "WindowControlService"] in calls
    assert ["sc.exe", "delete", "WindowControlService"] in calls
    assert r"C:\ProgramData\EmuCtrl\unlock.dat" in removed_files
    assert r"C:\ProgramData\WindowControl\unlock.dat" in removed_files


