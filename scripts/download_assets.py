"""
Download mediamtx and scrcpy Windows binaries into assets/ if missing.

Usage:
    uv run python scripts/download_assets.py

Downloads:
    assets/mediamtx/mediamtx.exe   — from bluenviron/mediamtx releases
    assets/scrcpy/scrcpy.exe       — from Genymobile/scrcpy releases
"""

import io
import os
import shutil
import sys
import urllib.request
import zipfile

# Pinned versions — bump here to upgrade
MEDIAMTX_VERSION = "v1.9.1"
SCRCPY_VERSION = "v3.1"

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "src", "assets")

DOWNLOADS = [
    {
        "name": "mediamtx",
        "url": (
            f"https://github.com/bluenviron/mediamtx/releases/download/"
            f"{MEDIAMTX_VERSION}/mediamtx_{MEDIAMTX_VERSION}_windows_amd64.zip"
        ),
        "dest_dir": os.path.join(ASSETS_DIR, "mediamtx"),
        "check": os.path.join(ASSETS_DIR, "mediamtx", "mediamtx.exe"),
        "extract": "mediamtx.exe",  # file inside the zip to extract
    },
    {
        "name": "scrcpy",
        "url": (
            f"https://github.com/Genymobile/scrcpy/releases/download/"
            f"{SCRCPY_VERSION}/scrcpy-win64-{SCRCPY_VERSION}.zip"
        ),
        "dest_dir": os.path.join(ASSETS_DIR, "scrcpy"),
        "check": os.path.join(ASSETS_DIR, "scrcpy", "scrcpy.exe"),
        "extract": None,  # extract entire zip contents
    },
]


def _download(url: str, label: str) -> bytes:
    print(f"  Downloading {label}...")
    req = urllib.request.Request(url, headers={"User-Agent": "window-control-setup"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length", 0))
        buf = io.BytesIO()
        downloaded = 0
        chunk_size = 65536
        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            buf.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded * 100 // total
                print(f"\r  {pct}% ({downloaded // 1024}KB / {total // 1024}KB)", end="", flush=True)
        print()
    return buf.getvalue()


def _extract(data: bytes, dest_dir: str, only_file: str | None):
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        if only_file:
            # Extract single file, stripping any directory prefix
            members = [m for m in zf.namelist() if m.endswith(only_file)]
            if not members:
                print(f"  ERROR: {only_file!r} not found in zip", file=sys.stderr)
                sys.exit(1)
            member = members[0]
            data = zf.read(member)
            dest = os.path.join(dest_dir, only_file)
            with open(dest, "wb") as f:
                f.write(data)
            print(f"  Extracted: {dest}")
        else:
            # Extract all, stripping the top-level zip directory
            names = zf.namelist()
            # Find common prefix directory (scrcpy zips as scrcpy-win64-vX.Y/)
            prefix = names[0].split("/")[0] + "/" if "/" in names[0] else ""
            for member in names:
                if member.endswith("/"):
                    continue
                rel = member[len(prefix):] if member.startswith(prefix) else member
                if not rel:
                    continue
                dest = os.path.join(dest_dir, rel)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with zf.open(member) as src, open(dest, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            print(f"  Extracted {len(names)} files to {dest_dir}")


def main():
    missing = [d for d in DOWNLOADS if not os.path.exists(d["check"])]
    if not missing:
        print("All assets present — nothing to download.")
        return

    for item in missing:
        print(f"\n[{item['name']}] {item['check']} missing")
        data = _download(item["url"], item["name"])
        _extract(data, item["dest_dir"], item["extract"])
        if not os.path.exists(item["check"]):
            print(f"  ERROR: expected {item['check']} after extract", file=sys.stderr)
            sys.exit(1)
        print(f"  OK: {item['name']} ready")

    print("\nAll assets downloaded.")


if __name__ == "__main__":
    main()
