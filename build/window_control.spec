# build/window_control.spec
# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_submodules

block_cipher = None

# SPECPATH is set by PyInstaller to the directory containing this spec file
_root = Path(SPECPATH).parent
src_dir = str(_root / 'src')

# Collect pywin32 fully (DLLs + pyd files + submodules)
win32_datas, win32_binaries, win32_hiddenimports = collect_all('win32')
win32api_datas, win32api_binaries, win32api_hiddenimports = collect_all('win32api')
pywintypes_datas, pywintypes_binaries, pywintypes_hiddenimports = collect_all('pywintypes')

# Bundle turbojpeg.dll if present (ships with PyTurboJPEG on Windows)
import glob, os
_turbojpeg_binaries = []
try:
    import turbojpeg as _tj_mod
    _tj_dir = os.path.dirname(_tj_mod.__file__)
    for _dll in glob.glob(os.path.join(_tj_dir, 'turbojpeg*.dll')):
        _turbojpeg_binaries.append((_dll, '.'))
except ImportError:
    pass

a = Analysis(
    [str(_root / 'src' / 'main.py')],
    pathex=[src_dir],
    binaries=win32_binaries + win32api_binaries + pywintypes_binaries + _turbojpeg_binaries,
    datas=[
        (str(_root / 'src' / 'client'), 'client'),
        (str(_root / 'src' / 'assets'), 'assets'),
    ] + win32_datas + win32api_datas + pywintypes_datas,
    hiddenimports=[
        'uvicorn.logging',
        'uvicorn.loops',
        'uvicorn.loops.auto',
        'uvicorn.protocols',
        'uvicorn.protocols.http',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.websockets',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan',
        'uvicorn.lifespan.on',
        'fastapi',
        'starlette',
        'pystray',
        'PIL',
        'qrcode',
        'mss',
        'numpy',
        'win32gui',
        'win32api',
        'win32con',
        'win32process',
        'win32com',
        'pywintypes',
        'win32service',
        'win32serviceutil',
        'win32ts',
        'win32security',
        'win32event',
        'win32pipe',
        'win32file',
        'servicemanager',
        'service',
        'service.pipe_server',
        'service.pipe_client',
        'service.desktop_monitor',
        'service.auto_unlock',
        'service_main',
        'dxcam',
        'keyring',
        'keyring.backends',
        'keyring.backends.Windows',
    ] + win32_hiddenimports + win32api_hiddenimports + pywintypes_hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,   # one-dir mode: DLLs live beside the exe, not extracted at runtime
    name='WindowControl',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,            # Services must run as console subsystem; GUI subsystem breaks SCM dispatch
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(_root / 'src' / 'assets' / 'icon.ico'),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='WindowControl',
)
