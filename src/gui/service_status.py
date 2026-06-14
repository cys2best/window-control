import sys

if sys.platform == "win32":
    import win32service
    import win32serviceutil

    SERVICE_NAME = "WindowControlService"

    def get_service_status() -> str:
        """Returns 'running', 'stopped', 'not_installed'."""
        try:
            status = win32serviceutil.QueryServiceStatus(SERVICE_NAME)
            state = status[1]
            if state == win32service.SERVICE_RUNNING:
                return "running"
            elif state in (win32service.SERVICE_STOPPED, win32service.SERVICE_STOP_PENDING):
                return "stopped"
            return "stopped"
        except Exception:
            return "not_installed"

    def is_service_installed() -> bool:
        return get_service_status() != "not_installed"

else:
    def get_service_status() -> str:
        return "not_installed"

    def is_service_installed() -> bool:
        return False
