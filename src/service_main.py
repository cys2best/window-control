# src/service_main.py
"""
WindowControl Windows Service.

Usage:
  WindowControl.exe --install     Install and start the service
  WindowControl.exe --uninstall   Stop and remove the service
  WindowControl.exe --start       Start an installed service
  WindowControl.exe --stop        Stop the running service
  (no args)                       Run as service (called by SCM)
"""
import sys
import os

# Log import crashes — service process dying here produces error 1053 with no other trace
def _log_crash(msg: str):
    for _p in [r"C:\ProgramData\WindowControl", r"C:\Windows\Temp", r"C:\Temp"]:
        try:
            os.makedirs(_p, exist_ok=True)
            with open(os.path.join(_p, "service_crash.log"), "a") as _f:
                _f.write(msg + "\n")
            return
        except Exception:
            continue

try:
    import threading
    import time
    import uvicorn
except Exception as _e:
    import traceback as _tb
    _log_crash(f"[stdlib/uvicorn import] {_tb.format_exc()}")
    raise

if sys.platform == "win32":
    try:
        import win32service
        import win32serviceutil
    except Exception as _e:
        import traceback as _tb
        _log_crash(f"[win32 import] {_tb.format_exc()}")
        raise

try:
    from config import PORT, QUALITY_MAP, DEFAULT_QUALITY
    from server.app import create_app
    from server.stream import CaptureState, FrameQueue, capture_loop
    from server.window_manager import list_windows
    from service.pipe_server import PipeServer
    from service.desktop_monitor import DesktopMonitor
    from service.auto_unlock import auto_unlock_on_lock, turn_monitor_off_after_unlock
except Exception as _e:
    import traceback as _tb
    _log_crash(f"[app import] {_tb.format_exc()}")
    raise


SERVICE_NAME = "WindowControlService"
SERVICE_DISPLAY = "Window Control Streaming Service"
SERVICE_DESCRIPTION = "Streams Windows application windows to iPhone over Tailscale. Continues during lock screen."


def _build_windows():
    windows = list_windows()
    return [{"id": w.hwnd, "title": w.title} for w in windows]


if sys.platform == "win32":
    import ctypes
    import ctypes.wintypes

    # Pure ctypes Win32 service — no pywin32 framework, no HandleCommandLine
    _advapi32 = ctypes.windll.advapi32

    SERVICE_CONTROL_STOP = 0x00000001
    SERVICE_CONTROL_INTERROGATE = 0x00000004
    SERVICE_RUNNING = 0x00000004
    SERVICE_STOP_PENDING = 0x00000003
    SERVICE_ACCEPT_STOP = 0x00000001

    class SERVICE_STATUS(ctypes.Structure):
        _fields_ = [
            ("dwServiceType",             ctypes.wintypes.DWORD),
            ("dwCurrentState",            ctypes.wintypes.DWORD),
            ("dwControlsAccepted",        ctypes.wintypes.DWORD),
            ("dwWin32ExitCode",           ctypes.wintypes.DWORD),
            ("dwServiceSpecificExitCode", ctypes.wintypes.DWORD),
            ("dwCheckPoint",              ctypes.wintypes.DWORD),
            ("dwWaitHint",                ctypes.wintypes.DWORD),
        ]

    _g_status_handle = None
    _g_stop_event = None

    def _set_service_status(state, controls=SERVICE_ACCEPT_STOP):
        global _g_status_handle
        if _g_status_handle is None:
            return
        ss = SERVICE_STATUS()
        ss.dwServiceType = 0x10  # SERVICE_WIN32_OWN_PROCESS
        ss.dwCurrentState = state
        ss.dwControlsAccepted = controls if state == SERVICE_RUNNING else 0
        ss.dwWin32ExitCode = 0
        ss.dwServiceSpecificExitCode = 0
        ss.dwCheckPoint = 0
        ss.dwWaitHint = 5000
        _advapi32.SetServiceStatus(_g_status_handle, ctypes.byref(ss))

    HANDLER_FUNC = ctypes.WINFUNCTYPE(None, ctypes.wintypes.DWORD)

    def _make_ctrl_handler(stop_event):
        def _handler(ctrl):
            if ctrl in (SERVICE_CONTROL_STOP, SERVICE_CONTROL_INTERROGATE):
                _set_service_status(SERVICE_STOP_PENDING)
                stop_event.set()
        return HANDLER_FUNC(_handler)

    SERVICE_MAIN_FUNC = ctypes.WINFUNCTYPE(None, ctypes.wintypes.DWORD, ctypes.POINTER(ctypes.c_wchar_p))

    def _run_service_body():
        """Core service logic — runs after SCM handshake complete."""
        state = CaptureState()
        state.set_quality(QUALITY_MAP[DEFAULT_QUALITY])
        frame_queue = FrameQueue()
        available_windows = []
        server = None
        pipe_server = None
        desktop_monitor = None

        def start_streaming():
            nonlocal server
            state.running = True
            available_windows.clear()
            available_windows.extend(_build_windows())
            fastapi_app = create_app(state, frame_queue, available_windows)
            config = uvicorn.Config(fastapi_app, host="0.0.0.0", port=PORT,
                                    log_level="warning", log_config=None)
            server = uvicorn.Server(config)
            threading.Thread(target=capture_loop, args=(state, frame_queue), daemon=True).start()
            threading.Thread(target=server.run, daemon=True).start()

        def on_lock():
            state.set_desktop("Winlogon")
            auto_unlock_on_lock()

        def on_unlock():
            state.set_desktop("Default")
            available_windows.clear()
            available_windows.extend(_build_windows())
            turn_monitor_off_after_unlock()

        def on_command(msg):
            cmd = msg.get("cmd")
            if cmd == "ping":
                return {"event": "pong"}
            elif cmd == "start":
                if not state.running:
                    start_streaming()
                return {"event": "state", "streaming": True}
            elif cmd == "stop":
                state.running = False
                if server:
                    server.should_exit = True
                return {"event": "state", "streaming": False}
            elif cmd == "select":
                hwnd = msg.get("id")
                if hwnd:
                    state.set_hwnd(hwnd)
                return {"event": "state", "streaming": state.running, "hwnd": hwnd}
            elif cmd == "quality":
                state.set_quality(msg.get("value", 85))
                return {"event": "pong"}
            elif cmd == "windows":
                available_windows.clear()
                available_windows.extend(_build_windows())
                return {"event": "windows", "list": available_windows}
            return None

        desktop_monitor = DesktopMonitor(on_lock=on_lock, on_unlock=on_unlock)
        desktop_monitor.start()

        pipe_server = PipeServer(on_command=on_command)
        pipe_server.start()

        start_streaming()

        _g_stop_event.wait()

        state.running = False
        if server:
            server.should_exit = True
        if pipe_server:
            pipe_server.stop()
        if desktop_monitor:
            desktop_monitor.stop()

    def _service_main(argc, argv):
        global _g_status_handle, _g_stop_event
        _log_crash(f"[service_main] SCM dispatched, registering handler")
        _g_stop_event = threading.Event()
        handler = _make_ctrl_handler(_g_stop_event)
        _g_status_handle = _advapi32.RegisterServiceCtrlHandlerW(SERVICE_NAME, handler)
        if not _g_status_handle:
            _log_crash(f"[service_main] RegisterServiceCtrlHandlerW failed: {ctypes.GetLastError()}")
            return
        _log_crash(f"[service_main] handler registered, reporting SERVICE_RUNNING")
        _set_service_status(SERVICE_RUNNING)
        _log_crash(f"[service_main] SERVICE_RUNNING reported, starting body")
        try:
            _run_service_body()
        except Exception:
            import traceback
            _log_crash(f"[service_main] body crashed: {traceback.format_exc()}")
        _set_service_status(SERVICE_STOP_PENDING, 0)
        _log_crash(f"[service_main] done")

    def _dispatch_service():
        """Called in --run-service path. Registers service main with SCM."""
        _svc_main_func = SERVICE_MAIN_FUNC(_service_main)
        SERVICE_TABLE_ENTRY = ctypes.c_void_p * 4
        table = SERVICE_TABLE_ENTRY(
            ctypes.cast(ctypes.create_unicode_buffer(SERVICE_NAME), ctypes.c_void_p),
            ctypes.cast(_svc_main_func, ctypes.c_void_p),
            None, None,
        )
        _log_crash(f"[dispatch] calling StartServiceCtrlDispatcherW")
        ret = _advapi32.StartServiceCtrlDispatcherW(table)
        if not ret:
            err = ctypes.GetLastError()
            _log_crash(f"[dispatch] StartServiceCtrlDispatcherW returned 0, error={err}")
        else:
            _log_crash(f"[dispatch] StartServiceCtrlDispatcherW returned cleanly")


def _remove_service_if_exists():
    """Stop and remove existing service — idempotent."""
    try:
        win32serviceutil.StopService(SERVICE_NAME)
        time.sleep(2)
    except Exception:
        pass
    try:
        win32serviceutil.RemoveService(SERVICE_NAME)
        time.sleep(1)
    except Exception:
        pass


def _install_service_manually():
    """Register service directly via win32service API with explicit binary path."""
    import traceback as _tb
    exe = sys.executable if getattr(sys, "frozen", False) else sys.argv[0]
    bin_path = f'"{exe}" --run-service'
    _log_crash(f"[install] registering bin_path={bin_path}")
    try:
        hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
        try:
            hsvc = win32service.CreateService(
                hscm,
                SERVICE_NAME,
                SERVICE_DISPLAY,
                win32service.SERVICE_ALL_ACCESS,
                win32service.SERVICE_WIN32_OWN_PROCESS,
                win32service.SERVICE_AUTO_START,
                win32service.SERVICE_ERROR_NORMAL,
                bin_path,
                None, 0, None, None, None,
            )
            try:
                win32service.ChangeServiceConfig2(
                    hsvc,
                    win32service.SERVICE_CONFIG_DESCRIPTION,
                    SERVICE_DESCRIPTION,
                )
                win32service.ChangeServiceConfig2(
                    hsvc,
                    win32service.SERVICE_CONFIG_FAILURE_ACTIONS,
                    {
                        "ResetPeriod": 86400,
                        "RebootMsg": "",
                        "Command": "",
                        "Actions": [
                            (win32service.SC_ACTION_RESTART, 60000),
                            (win32service.SC_ACTION_RESTART, 60000),
                            (win32service.SC_ACTION_RESTART, 60000),
                        ],
                    },
                )
                _log_crash(f"[install] CreateService OK")
            finally:
                win32service.CloseServiceHandle(hsvc)
        finally:
            win32service.CloseServiceHandle(hscm)
    except Exception:
        _log_crash(f"[install] CreateService FAILED: {_tb.format_exc()}")


def main():
    _log_crash(f"[main] argv={sys.argv} frozen={getattr(sys, 'frozen', False)}")
    if "--install" in sys.argv:
        _remove_service_if_exists()
        _install_service_manually()
        print(f"Service '{SERVICE_NAME}' installed.")
        try:
            win32serviceutil.StartService(SERVICE_NAME)
            print(f"Service '{SERVICE_NAME}' started.")
        except Exception as exc:
            _log_crash(f"[StartService] {exc}")
            print(f"Service start failed (starts on next reboot): {exc}")
    elif "--uninstall" in sys.argv:
        try:
            win32serviceutil.StopService(SERVICE_NAME)
            time.sleep(2)
        except Exception:
            pass
        try:
            win32serviceutil.RemoveService(SERVICE_NAME)
            print(f"Service '{SERVICE_NAME}' removed.")
        except Exception as exc:
            print(f"Remove failed: {exc}")
    elif "--start" in sys.argv:
        win32serviceutil.StartService(SERVICE_NAME)
    elif "--stop" in sys.argv:
        win32serviceutil.StopService(SERVICE_NAME)
    elif "--run-service" in sys.argv:
        # SCM entry point — pure ctypes dispatch, no pywin32 framework
        _log_crash(f"[run-service] entered, exe={sys.executable}")
        _dispatch_service()
    else:
        print("Usage: WindowControl.exe --install | --uninstall | --start | --stop")


if __name__ == "__main__":
    main()
