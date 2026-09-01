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

    monkeypatch.setattr(main_mod.config, "engine_exe_path", lambda: "C:/app/engine.exe")
    monkeypatch.setattr(main_mod.os.path, "isfile", lambda _: True)
    monkeypatch.setattr(main_mod.secrets, "token_hex", lambda size: "generated-whep")
    monkeypatch.setattr(main_mod.config, "VPS_SIGNALING_URL", "wss://signal.example/ws")
    monkeypatch.setattr(main_mod.config, "ENGINE_SIGNALING_SECRET", "signal-secret", raising=False)
    monkeypatch.setattr(main_mod.config, "ENGINE_LOCAL_ICE_SERVERS", ("stun:local", "turn:local"), raising=False)
    monkeypatch.setattr(main_mod.config, "ENGINE_PUBLIC_ICE_SERVERS", ("stun:public", "turn:public"), raising=False)

    orchestrator = main_mod.build_engine_orchestrator()

    assert orchestrator.config.exe_path == "C:/app/engine.exe"
    assert orchestrator.config.whep_secret == "generated-whep"
    assert orchestrator.config.signaling_url == "wss://signal.example/ws"
    assert orchestrator.config.signaling_secret == "signal-secret"
    assert orchestrator.config.local_ice_servers == ("stun:local", "turn:local")
    assert orchestrator.config.public_ice_servers == ("stun:public", "turn:public")


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


class _FakeState:
    def set_quality(self, quality):
        pass


class _FakeLauncher:
    quality_changed = type("Signal", (), {"connect": lambda self, callback: None})()

    def __init__(self, state):
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
    monkeypatch.setattr(main_mod, "CaptureState", _FakeState)
    monkeypatch.setattr(main_mod, "FrameQueue", object)
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


def test_config_imports():
    from config import (PORT, VERSION, QUALITY_MAP, DEFAULT_QUALITY,
                        SCRCPY_PATH, CLIENT_DIR, ASSETS_DIR, engine_exe_path)
    assert PORT == 8080
    assert DEFAULT_QUALITY in QUALITY_MAP
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
