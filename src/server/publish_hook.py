"""Thin script mediamtx invokes via runOnDemand/runOnUnDemand.

Deliberately dependency-free (stdlib `urllib` only) so it runs under
whatever `python` mediamtx's spawned command finds on PATH -- it does not
need this project's venv. It POSTs to the already-running app.py process's
internal API and exits immediately; it is NOT a supervisor and does not
wait for the actual video pipeline to come up. mediamtx tracks that itself
by waiting (up to runOnDemandStartTimeout) for an RTSP publish to actually
land on the path, independent of when this script's own process exits.

Usage (wired into mediamtx.yml's pathDefaults by mediamtx_manager.py):
    runOnDemand:   <python> publish_hook.py start
    runOnUnDemand: <python> publish_hook.py stop
mediamtx sets MTX_PATH as an environment variable on the spawned process
for both hooks (confirmed against mediamtx's own docs) -- this script reads
it from os.environ rather than relying on shell variable expansion syntax,
since that syntax differs between the Windows and POSIX shells mediamtx
might invoke the command through.
"""
import os
import sys
import urllib.request

# Hand-copied duplicate of config.PORT -- deliberately NOT imported, since
# this script must stay stdlib-only (mediamtx spawns it with whatever python
# is on PATH, outside this project's venv/sys.path). Change both together.
APP_PORT = 8080


def main(action: str, path_name: str, base_url: str, opener=urllib.request.urlopen) -> None:
    url = f"{base_url}/internal/instances/{path_name}/publish/{action}"
    req = urllib.request.Request(url, method="POST")
    try:
        with opener(req, timeout=5):
            pass
    except Exception:
        # Best-effort: mediamtx doesn't retry this script on failure for
        # runOnUnDemand, and for runOnDemand a failure here just means the
        # path never gets a publisher and the client's WHEP request times
        # out -- there's no one left to report an exception to.
        pass


if __name__ == "__main__":
    action = sys.argv[1]
    path_name = os.environ.get("MTX_PATH", "")
    main(action, path_name, f"http://127.0.0.1:{APP_PORT}")
