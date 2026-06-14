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
    try:
        _p = r"C:\ProgramData\WindowControl"
        os.makedirs(_p, exist_ok=True)
        with open(os.path.join(_p, "service_crash.log"), "a") as _f:
            _f.write(msg + "\n")
    except Exception:
        pass

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


def _set_failure_actions():
    """Configure 3x auto-restart with 60s delay."""
    try:
        hscm = win32service.OpenSCManager(None, None, win32service.SC_MANAGER_ALL_ACCESS)
        hsvc = win32service.OpenService(hscm, SERVICE_NAME, win32service.SERVICE_ALL_ACCESS)
        try:
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
                }
            )
        finally:
            win32service.CloseServiceHandle(hsvc)
            win32service.CloseServiceHandle(hscm)
    except Exception:
        pass


def main():
    if "--install" in sys.argv:
        _remove_service_if_exists()
        # HandleCommandLine with 'install' does InstallPythonClassString correctly
        # even in a frozen exe — it resolves the class from the running module.
        sys.argv = [sys.argv[0], "install"]
        win32serviceutil.HandleCommandLine(WindowControlService)
        _set_failure_actions()
        win32serviceutil.StartService(SERVICE_NAME)
        print(f"Service '{SERVICE_NAME}' installed and started.")
    elif "--uninstall" in sys.argv:
        try:
            win32serviceutil.StopService(SERVICE_NAME)
            time.sleep(2)
        except Exception:
            pass
        sys.argv = [sys.argv[0], "remove"]
        win32serviceutil.HandleCommandLine(WindowControlService)
    elif "--start" in sys.argv:
        win32serviceutil.StartService(SERVICE_NAME)
    elif "--stop" in sys.argv:
        win32serviceutil.StopService(SERVICE_NAME)
    else:
        # SCM starts exe with no args — use dispatcher directly for frozen exe compatibility
        try:
            servicemanager.Initialize()
            servicemanager.PrepareToHostSingle(WindowControlService)
            servicemanager.StartServiceCtrlDispatcher()
        except Exception as exc:
            import traceback, os
            log_path = r"C:\ProgramData\WindowControl\service_crash.log"
            try:
                os.makedirs(r"C:\ProgramData\WindowControl", exist_ok=True)
                with open(log_path, "a") as f:
                    f.write(traceback.format_exc())
            except Exception:
                pass
            servicemanager.LogErrorMsg(f"WindowControl service crashed: {exc}")
            raise


if __name__ == "__main__":
    main()
