import threading
import tempfile
import os
import subprocess
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


def _get_asset_url(version: str) -> str:
    return (
        f"https://github.com/{GITHUB_REPO}/releases/download/"
        f"v{version}/WindowControlInstaller.exe"
    )


def download_and_install(version: str, on_progress=None, on_error=None):
    """Download installer for `version` to %TEMP% and run it silently.

    on_progress(pct: int): called with 0-100 during download
    on_error(msg: str): called on any failure
    """
    if on_progress is None:
        on_progress = lambda _: None
    if on_error is None:
        on_error = lambda _: None

    def _run():
        url = _get_asset_url(version)
        dest = os.path.join(tempfile.gettempdir(), "WindowControlInstaller.exe")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "WindowControl"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                chunk_size = 8192
                with open(dest, "wb") as f:
                    while True:
                        chunk = resp.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            on_progress(min(100, int(downloaded * 100 / total)))
        except Exception as e:
            on_error(str(e))
            return
        try:
            subprocess.Popen([dest, "/SILENT", "/NORESTART"])
        except Exception as e:
            on_error(str(e))

    t = threading.Thread(target=_run, daemon=True)
    t.start()


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

    threading.Thread(target=_run, daemon=True).start()
