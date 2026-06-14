#!/usr/bin/env python3
"""Sync version from src/config.py → pyproject.toml + installer.iss."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
CONFIG = ROOT / "src" / "config.py"
PYPROJECT = ROOT / "pyproject.toml"
ISS = ROOT / "build" / "installer.iss"


def read_version() -> str:
    match = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', CONFIG.read_text(), re.M)
    if not match:
        sys.exit("ERROR: VERSION not found in src/config.py")
    return match.group(1)


def update_pyproject(version: str):
    text = PYPROJECT.read_text()
    updated = re.sub(r'^version\s*=\s*"[^"]+"', f'version = "{version}"', text, flags=re.M)
    PYPROJECT.write_text(updated)


def update_iss(version: str):
    text = ISS.read_text()
    updated = re.sub(r'#define MyAppVersion "[^"]+"', f'#define MyAppVersion "{version}"', text)
    ISS.write_text(updated)


if __name__ == "__main__":
    version = read_version()
    update_pyproject(version)
    update_iss(version)
    print(f"Version synced to {version} in pyproject.toml and installer.iss")
