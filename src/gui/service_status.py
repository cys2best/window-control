import subprocess
import sys

if sys.platform == "win32":
    SERVICE_NAME = "WindowControlService"

    def get_service_status() -> str:
        """Returns 'running', 'stopped', 'not_installed'."""
        try:
            r = subprocess.run(
                ["sc.exe", "query", SERVICE_NAME],
                capture_output=True, text=True, timeout=5
            )
            out = r.stdout
            if "does not exist" in out or r.returncode == 1060:
                return "not_installed"
            if "RUNNING" in out:
                return "running"
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
