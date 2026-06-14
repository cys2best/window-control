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
        import win32event
        import servicemanager
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
    class WindowControlService(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = SERVICE_DISPLAY
        _svc_description_ = SERVICE_DESCRIPTION
        _svc_start_type_ = win32service.SERVICE_AUTO_START

        def __init__(self, args):
            win32serviceutil.ServiceFramework.__init__(self, args)
            self._stop_event = win32event.CreateEvent(None, 0, 0, None)
            try:
                self._state = CaptureState()
                self._state.set_quality(QUALITY_MAP[DEFAULT_QUALITY])
                self._frame_queue = FrameQueue()
                self._available_windows = []
                self._server = None
                self._pipe_server = None
                self._desktop_monitor = None
            except Exception as exc:
                import traceback
                servicemanager.LogErrorMsg(f"WindowControl __init__ crashed: {exc}\n{traceback.format_exc()}")
                raise

        def SvcDoRun(self):
            self.ReportServiceStatus(win32service.SERVICE_RUNNING)
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STARTED,
                (self._svc_name_, "")
            )
            try:
                self._run()
            except Exception as exc:
                import traceback
                servicemanager.LogErrorMsg(f"WindowControl SvcDoRun crashed: {exc}\n{traceback.format_exc()}")
                raise
            servicemanager.LogMsg(
                servicemanager.EVENTLOG_INFORMATION_TYPE,
                servicemanager.PYS_SERVICE_STOPPED,
                (self._svc_name_, "")
            )

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            self._state.running = False
            if self._server:
                self._server.should_exit = True
            if self._pipe_server:
                self._pipe_server.stop()
            if self._desktop_monitor:
                self._desktop_monitor.stop()
            win32event.SetEvent(self._stop_event)

        def _on_lock(self):
            self._state.set_desktop("Winlogon")
            servicemanager.LogInfoMsg("WindowControl: session locked, streaming lock screen")
            auto_unlock_on_lock()

        def _on_unlock(self):
            self._state.set_desktop("Default")
            self._available_windows.clear()
            self._available_windows.extend(_build_windows())
            servicemanager.LogInfoMsg("WindowControl: session unlocked, resuming normal stream")
            turn_monitor_off_after_unlock()

        def _on_command(self, msg: dict) -> dict | None:
            cmd = msg.get("cmd")
            if cmd == "ping":
                return {"event": "pong"}
            elif cmd == "start":
                if not self._state.running:
                    self._start_streaming()
                return {"event": "state", "streaming": True, "locked": self._state.desktop == "Winlogon"}
            elif cmd == "stop":
                self._state.running = False
                if self._server:
                    self._server.should_exit = True
                return {"event": "state", "streaming": False, "locked": self._state.desktop == "Winlogon"}
            elif cmd == "select":
                hwnd = msg.get("id")
                if hwnd:
                    self._state.set_hwnd(hwnd)
                return {"event": "state", "streaming": self._state.running, "hwnd": hwnd}
            elif cmd == "quality":
                self._state.set_quality(msg.get("value", 85))
                return {"event": "pong"}
            elif cmd == "windows":
                self._available_windows.clear()
                self._available_windows.extend(_build_windows())
                return {"event": "windows", "list": self._available_windows}
            return None

        def _start_streaming(self):
            self._state.running = True
            self._available_windows.clear()
            self._available_windows.extend(_build_windows())

            fastapi_app = create_app(
                self._state, self._frame_queue, self._available_windows
            )
            config = uvicorn.Config(
                fastapi_app, host="0.0.0.0", port=PORT,
                log_level="warning", log_config=None
            )
            self._server = uvicorn.Server(config)

            threading.Thread(
                target=capture_loop,
                args=(self._state, self._frame_queue),
                daemon=True,
            ).start()
            threading.Thread(target=self._server.run, daemon=True).start()

        def _run(self):
            self._desktop_monitor = DesktopMonitor(
                on_lock=self._on_lock,
                on_unlock=self._on_unlock,
            )
            self._desktop_monitor.start()

            self._pipe_server = PipeServer(on_command=self._on_command)
            self._pipe_server.start()

            self._start_streaming()

            win32event.WaitForSingleObject(self._stop_event, win32event.INFINITE)


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
        # SCM entry point — always explicit now, never ambiguous
        _log_crash(f"[run-service] entered, exe={sys.executable}")
        try:
            servicemanager.Initialize(SERVICE_NAME, sys.executable)
            servicemanager.PrepareToHostSingle(WindowControlService)
            servicemanager.StartServiceCtrlDispatcher()
        except Exception as exc:
            import traceback
            _log_crash(f"[SCM dispatch] {traceback.format_exc()}")
            raise
    else:
        # Dev / interactive fallback
        win32serviceutil.HandleCommandLine(WindowControlService)


if __name__ == "__main__":
    main()
