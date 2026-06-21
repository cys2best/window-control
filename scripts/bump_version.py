#!/usr/bin/env python3
"""Sync version: git tag → config.py + pyproject.toml + installer.iss.

In CI (GITHUB_REF=refs/tags/v1.2.22): extracts "1.2.22", writes everywhere.
Locally (no tag env): reads config.py and syncs outward only.
"""
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
CONFIG = ROOT / "src" / "config.py"
PYPROJECT = ROOT / "pyproject.toml"
ISS = ROOT / "build" / "installer.iss"


def version_from_tag():
    ref = os.environ.get("GITHUB_REF", "")
    if ref.startswith("refs/tags/v"):
        return ref[len("refs/tags/v"):]
    return None


def read_config_version():
    match = re.search(r'^VERSION\s*=\s*["\']([^"\']+)["\']', CONFIG.read_text(), re.M)
    if not match:
        sys.exit("ERROR: VERSION not found in src/config.py")
    return match.group(1)


def update_config(version):
    text = CONFIG.read_text()
    updated = re.sub(
        r'^VERSION\s*=\s*["\'][^"\']+["\']',
        f'VERSION = "{version}"',
        text, flags=re.M
    )
    CONFIG.write_text(updated)


def update_pyproject(version):
    text = PYPROJECT.read_text()
    updated = re.sub(r'^version\s*=\s*"[^"]+"', f'version = "{version}"', text, flags=re.M)
    PYPROJECT.write_text(updated)


def update_iss(version):
    if not ISS.exists():
        return
    text = ISS.read_text()
    updated = re.sub(r'#define MyAppVersion "[^"]+"', f'#define MyAppVersion "{version}"', text)
    ISS.write_text(updated)


if __name__ == "__main__":
    tag_version = version_from_tag()
    if tag_version:
        print(f"Tag version: {tag_version}")
        update_config(tag_version)
        version = tag_version
    else:
        version = read_config_version()
        print(f"No tag — using config.py: {version}")

    update_pyproject(version)
    update_iss(version)
    print(f"Version synced to {version}")
