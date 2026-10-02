# apps/desktop/conftest.py
# Empty on purpose: its presence is what makes pytest treat apps/desktop as
# its own import root (no __init__.py here), so test_window.py's
# `from window import DesktopWindow` and test_tray.py's `from tray import
# TrayIcon` resolve without needing package-qualified imports -- same
# "prepend this directory to sys.path" behavior the repo already relies on
# implicitly for tests/ via pyproject.toml's pythonpath = ["src"].
