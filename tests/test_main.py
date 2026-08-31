import sys
import pytest


def test_build_engine_orchestrator_disabled(monkeypatch):
    import main as main_mod

    monkeypatch.setattr(main_mod.config, "ENGINE_EXE_PATH", "", raising=False)

    assert main_mod.build_engine_orchestrator() is None


def test_build_engine_orchestrator_uses_one_generated_launch_secret(monkeypatch):
    import main as main_mod

    token_sizes = []
    monkeypatch.setattr(main_mod.config, "ENGINE_EXE_PATH", "C:/app/engine.exe", raising=False)
    monkeypatch.setattr(main_mod.config, "ENGINE_WHEP_CAPABILITY_SECRET", "", raising=False)
    monkeypatch.setattr(
        main_mod.secrets,
        "token_hex",
        lambda size: token_sizes.append(size) or "a" * (size * 2),
    )

    orchestrator = main_mod.build_engine_orchestrator()

    assert orchestrator.config.whep_secret == "a" * 64
    assert token_sizes == [32]


def test_build_engine_orchestrator_passes_configured_runtime_values(monkeypatch):
    import main as main_mod

    monkeypatch.setattr(main_mod.config, "ENGINE_EXE_PATH", "C:/app/engine.exe", raising=False)
    monkeypatch.setattr(main_mod.config, "ENGINE_WHEP_CAPABILITY_SECRET", "configured-whep", raising=False)
    monkeypatch.setattr(main_mod.config, "VPS_SIGNALING_URL", "wss://signal.example/ws")
    monkeypatch.setattr(main_mod.config, "ENGINE_SIGNALING_SECRET", "signal-secret", raising=False)
    monkeypatch.setattr(main_mod.config, "ENGINE_LOCAL_ICE_SERVERS", ("stun:local", "turn:local"), raising=False)
    monkeypatch.setattr(main_mod.config, "ENGINE_PUBLIC_ICE_SERVERS", ("stun:public", "turn:public"), raising=False)

    orchestrator = main_mod.build_engine_orchestrator()

    assert orchestrator.config.exe_path == "C:/app/engine.exe"
    assert orchestrator.config.whep_secret == "configured-whep"
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


def test_main_builds_engine_orchestrator_once_and_passes_it_to_manager(monkeypatch):
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
    assert manager_calls == [
        ((), {"mediamtx": None, "engine_orchestrator": orchestrator})
    ]


@pytest.mark.parametrize(
    ("backend", "expected_manager_call"),
    [
        ("aiortc", ((), {"mediamtx": None})),
        ("mediamtx", (("manager",), {})),
    ],
)
def test_main_preserves_legacy_backend_manager_construction(
    monkeypatch, backend, expected_manager_call
):
    import main as main_mod

    manager_calls = []
    _patch_main_startup(monkeypatch, main_mod, manager_calls)
    monkeypatch.setattr(main_mod, "build_engine_orchestrator", lambda: None)
    monkeypatch.setattr(main_mod.config, "WEBRTC_BACKEND", backend)
    monkeypatch.setattr(main_mod, "MediamtxManager", lambda: "manager")
    monkeypatch.setattr(main_mod.sys, "argv", ["main.py"])

    with pytest.raises(SystemExit) as exit_info:
        main_mod.main()

    assert exit_info.value.code == 0
    assert manager_calls == [expected_manager_call]


def test_config_imports():
    """config.py exports expected constants including new mediamtx config."""
    from config import (PORT, VERSION, QUALITY_MAP, DEFAULT_QUALITY,
                        MEDIAMTX_PORT, WHEP_PORT, SCRCPY_PATH, MEDIAMTX_PATH,
                        CLIENT_DIR, ASSETS_DIR)
    assert PORT == 8080
    assert MEDIAMTX_PORT == 8554
    assert WHEP_PORT == 8889
    assert DEFAULT_QUALITY in QUALITY_MAP


def test_instance_name_emulator():
    from server.instance_manager import instance_name
    assert instance_name("emulator-5554") == "instance0"
    assert instance_name("emulator-5556") == "instance1"
    assert instance_name("emulator-5558") == "instance2"


def test_instance_name_non_emulator():
    from server.instance_manager import instance_name
    name = instance_name("192.168.1.100:5555")
    assert name.startswith("instance_")
