import threading
import urllib.request
import json
from packaging.version import Version

from config import VERSION, GITHUB_REPO


def _fetch_latest_version() -> str | None:
    url = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "WindowControl"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        tag = data.get("tag_name", "")
        return tag.lstrip("v")
    except Exception:
        return None


def check_for_update(on_update_available):
    """Run in background. Calls on_update_available(latest_version) if newer release exists."""
    def _run():
        latest = _fetch_latest_version()
        if latest is None:
            return
        try:
            if Version(latest) > Version(VERSION):
                on_update_available(latest)
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True)
    t.start()
