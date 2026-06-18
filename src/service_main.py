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

_log_crash(f"[imports-start] pid={os.getpid()} user={os.environ.get('USERNAME','?')} path={os.environ.get('PATH','?')[:120]}")

try:
    import subprocess
    import threading
    import time
    _log_crash("[imports] threading+time OK")
except Exception as _e:
    import traceback as _tb
    _log_crash(f"[stdlib import] {_tb.format_exc()}")
    raise

if sys.platform == "win32":
    _log_crash("[imports] win32 (ctypes only, no pywin32)")

try:
    _log_crash("[imports] loading app modules...")
    from service.pipe_server import PipeServer
    _log_crash("[imports] pipe_server OK")
    from service.desktop_monitor import DesktopMonitor
    _log_crash("[imports] desktop_monitor OK")
    from service.auto_unlock import auto_unlock_on_lock, turn_monitor_off_after_unlock
    _log_crash("[imports] auto_unlock OK")
except Exception as _e:
    import traceback as _tb
    _log_crash(f"[app import] {_tb.format_exc()}")
    raise


SERVICE_NAME = "WindowControlService"
SERVICE_DISPLAY = "Window Control Lock Screen Monitor"
SERVICE_DESCRIPTION = "Monitors Windows lock/unlock events for WindowControl screen streaming."


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
        if not _g_status_handle:
            return
        ss = SERVICE_STATUS()
        ss.dwServiceType = 0x110  # SERVICE_WIN32_OWN_PROCESS | SERVICE_INTERACTIVE_PROCESS
        ss.dwCurrentState = state
        ss.dwControlsAccepted = controls if state == SERVICE_RUNNING else 0
        ss.dwWin32ExitCode = 0
        ss.dwServiceSpecificExitCode = 0
        ss.dwCheckPoint = 0
        ss.dwWaitHint = 5000
        _advapi32.SetServiceStatus.restype = ctypes.wintypes.BOOL
        _advapi32.SetServiceStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(SERVICE_STATUS)]
        ret = _advapi32.SetServiceStatus(_g_status_handle, ctypes.byref(ss))
        if not ret:
            _log_crash(f"[SetServiceStatus] failed state={state} err={ctypes.GetLastError()}")

    HANDLER_FUNC = ctypes.WINFUNCTYPE(None, ctypes.wintypes.DWORD)

    def _make_ctrl_handler(stop_event):
        def _handler(ctrl):
            if ctrl in (SERVICE_CONTROL_STOP, SERVICE_CONTROL_INTERROGATE):
                _set_service_status(SERVICE_STOP_PENDING)
                stop_event.set()
        return HANDLER_FUNC(_handler)

    SERVICE_MAIN_FUNC = ctypes.WINFUNCTYPE(None, ctypes.wintypes.DWORD, ctypes.POINTER(ctypes.c_wchar_p))

    def _run_service_body():
        """Service body — monitors lock/unlock, pushes events to GUI via pipe.

        GUI process (running as logged-in user) owns uvicorn + capture.
        This service only handles WTS session notifications and auto-unlock,
        then forwards lock/unlock events so the GUI can switch capture desktops.
        """
        _log_crash("[service_body] started — desktop monitor + pipe server only")
        pipe_server = None
        desktop_monitor = None

        def on_lock():
            _log_crash("[desktop] LOCK detected")
            if pipe_server:
                pipe_server.push({"event": "lock"})
            def _after_unlock():
                _log_crash("[desktop] auto_unlock completed — pushing unlock event to GUI")
                if pipe_server:
                    pipe_server.push({"event": "unlock"})
            auto_unlock_on_lock(on_unlocked=_after_unlock)

        def on_unlock():
            _log_crash("[desktop] UNLOCK detected")
            turn_monitor_off_after_unlock()
            if pipe_server:
                pipe_server.push({"event": "unlock"})

        def on_command(msg):
            cmd = msg.get("cmd")
            if cmd == "ping":
                return {"event": "pong"}
            return None

        desktop_monitor = DesktopMonitor(on_lock=on_lock, on_unlock=on_unlock)
        desktop_monitor.start()

        pipe_server = PipeServer(on_command=on_command)
        pipe_server.start()

        _log_crash("[service_body] waiting for stop event")
        _g_stop_event.wait()

        if pipe_server:
            pipe_server.stop()
        if desktop_monitor:
            desktop_monitor.stop()

    def _service_main(argc, argv):
        global _g_status_handle, _g_stop_event
        _log_crash(f"[service_main] SCM dispatched, registering handler")
        _g_stop_event = threading.Event()
        handler = _make_ctrl_handler(_g_stop_event)
        # SERVICE_STATUS_HANDLE is not HANDLE — must declare as c_void_p or it gets truncated
        _advapi32.RegisterServiceCtrlHandlerW.restype = ctypes.c_void_p
        _advapi32.RegisterServiceCtrlHandlerW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
        _g_status_handle = _advapi32.RegisterServiceCtrlHandlerW(
            SERVICE_NAME, ctypes.cast(handler, ctypes.c_void_p)
        )
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

    class _SERVICE_TABLE_ENTRYW(ctypes.Structure):
        _fields_ = [
            ("lpServiceName", ctypes.c_wchar_p),
            ("lpServiceProc", ctypes.c_void_p),
        ]

    def _dispatch_service():
        """Called in --run-service path. Registers service main with SCM."""
        _svc_main_func = SERVICE_MAIN_FUNC(_service_main)
        # Two entries: the service entry + null terminator
        TableType = _SERVICE_TABLE_ENTRYW * 2
        table = TableType(
            _SERVICE_TABLE_ENTRYW(SERVICE_NAME, ctypes.cast(_svc_main_func, ctypes.c_void_p)),
            _SERVICE_TABLE_ENTRYW(None, None),
        )
        _advapi32.StartServiceCtrlDispatcherW.restype = ctypes.wintypes.BOOL
        _advapi32.StartServiceCtrlDispatcherW.argtypes = [ctypes.POINTER(_SERVICE_TABLE_ENTRYW)]
        _log_crash(f"[dispatch] calling StartServiceCtrlDispatcherW")
        ret = _advapi32.StartServiceCtrlDispatcherW(table)
        if not ret:
            err = ctypes.GetLastError()
            _log_crash(f"[dispatch] StartServiceCtrlDispatcherW returned 0, error={err}")
        else:
            _log_crash(f"[dispatch] StartServiceCtrlDispatcherW returned cleanly")


def _sc(args: list, timeout=10):
    """Run sc.exe with args, return (returncode, stdout+stderr)."""
    r = subprocess.run(
        ["sc.exe"] + args,
        capture_output=True, text=True, timeout=timeout
    )
    return r.returncode, (r.stdout + r.stderr).strip()


def _remove_service_if_exists():
    """Stop and remove existing service — idempotent."""
    _sc(["stop", SERVICE_NAME])
    time.sleep(2)
    _sc(["delete", SERVICE_NAME])
    time.sleep(1)


def _disable_lock_on_rdp_disconnect():
    """Write registry keys to prevent Windows locking on RDP disconnect.

    Runs elevated (called from --install which runs as admin).
    Sets the 'Don't lock on disconnect' equivalent — disables screen saver
    password requirement which is what triggers lock on RDP disconnect.
    """
    try:
        import winreg
        # Per-machine: disable inactivity lock timer
        k = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon",
            0, winreg.KEY_SET_VALUE
        )
        # DisableLockWorkstation via policy path (machine-wide, overrides user setting)
        winreg.CloseKey(k)

        # Machine policy override — prevents GPO from re-enabling screensaver lock
        try:
            k3 = winreg.CreateKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Policies\Microsoft\Windows\Control Panel\Desktop"
            )
            winreg.SetValueEx(k3, "ScreenSaverIsSecure", 0, winreg.REG_SZ, "0")
            winreg.SetValueEx(k3, "ScreenSaveActive", 0, winreg.REG_SZ, "0")
            winreg.CloseKey(k3)
        except Exception:
            pass

        _log_crash("[install] lock-on-disconnect disabled via registry")
    except Exception as e:
        _log_crash(f"[install] registry lock disable failed: {e}")


def _install_service_manually():
    """Register service via sc.exe with auto-restart failure actions."""
    exe = sys.executable if getattr(sys, "frozen", False) else sys.argv[0]
    bin_path = f'"{exe}" --run-service'
    _log_crash(f"[install] registering bin_path={bin_path}")

    rc, out = _sc([
        "create", SERVICE_NAME,
        "binPath=", bin_path,
        "DisplayName=", SERVICE_DISPLAY,
        "start=", "auto",
        "type=", "interact",
        "type=", "own",
    ])
    _log_crash(f"[install] sc create rc={rc} out={out}")

    # Set description
    _sc(["description", SERVICE_NAME, SERVICE_DESCRIPTION])

    # Auto-restart on failure (3× with 60s delay)
    rc2, out2 = _sc([
        "failure", SERVICE_NAME,
        "reset=", "86400",
        "actions=", "restart/60000/restart/60000/restart/60000",
    ])
    _log_crash(f"[install] sc failure rc={rc2} out={out2}")


def main():
    _log_crash(f"[main] argv={sys.argv} frozen={getattr(sys, 'frozen', False)}")
    if "--install" in sys.argv:
        _remove_service_if_exists()
        _install_service_manually()
        _disable_lock_on_rdp_disconnect()
        print(f"Service '{SERVICE_NAME}' installed.")
        rc, out = _sc(["start", SERVICE_NAME])
        if rc == 0:
            print(f"Service '{SERVICE_NAME}' started.")
        else:
            _log_crash(f"[StartService] rc={rc} out={out}")
            print(f"Service start failed (starts on next reboot): {out}")
    elif "--uninstall" in sys.argv:
        _sc(["stop", SERVICE_NAME])
        time.sleep(2)
        rc, out = _sc(["delete", SERVICE_NAME])
        if rc == 0:
            print(f"Service '{SERVICE_NAME}' removed.")
        else:
            print(f"Remove failed: {out}")
    elif "--start" in sys.argv:
        rc, out = _sc(["start", SERVICE_NAME])
        if rc != 0:
            print(f"Start failed: {out}")
    elif "--stop" in sys.argv:
        rc, out = _sc(["stop", SERVICE_NAME])
        if rc != 0:
            print(f"Stop failed: {out}")
    elif "--run-service" in sys.argv:
        # SCM entry point — pure ctypes dispatch, no pywin32 framework
        _log_crash(f"[run-service] entered, exe={sys.executable}")
        _dispatch_service()
    else:
        print("Usage: WindowControl.exe --install | --uninstall | --start | --stop")


if __name__ == "__main__":
    main()
