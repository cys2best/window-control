# build/window_control.spec
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

block_cipher = None

_root = Path(SPECPATH).parent
src_dir = str(_root / 'src')
desktop_dir = str(_root / 'apps' / 'desktop')

a = Analysis(
    [str(_root / 'src' / 'main.py')],
    pathex=[src_dir, desktop_dir],
    binaries=[],
    datas=[
        # apps/web's Next.js static export (npm run build -w apps/web),
        # staged as top-level "web" to match config.py's get_web_build_dir()
        # frozen-mode branch (os.path.join(BASE_PATH, "web")). Replaces the
        # old src/client -> "client" entry.
        (str(_root / 'apps' / 'web' / 'out'), 'web'),
        # 'assets' includes assets/engine (engine.exe + runtime DLLs staged by
        # build.bat) and assets/scrcpy (downloaded by download_assets.py).
        (str(_root / 'src' / 'assets'), 'assets'),
    ],
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
        'webview',
        # main.py imports it lazily, only on the --webview-window
        # re-invocation path (apps/desktop/window.py spawns that), so
        # PyInstaller's graph must be told about it explicitly.
        'webview_main',
        'PIL',
        'qrcode',
        'numpy',
        'nest_asyncio',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Pillow's AVIF codec (~7.5 MB); the app only reads PNG/ICO.
    excludes=['PIL._avif'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

_assets_dir = (_root / 'src' / 'assets').resolve()
# adb plus the server jar are all scrcpy_server.py uses; scrcpy's desktop
# client (scrcpy.exe, SDL2, ffmpeg DLLs) never runs.
_scrcpy_keep = {'adb.exe', 'AdbWinApi.dll', 'AdbWinUsbApi.dll', 'scrcpy-server'}
# The UI is plain Qt Widgets: no OpenGL fallbacks (ANGLE, software GL) and
# no Quick/QML/Network, which only the virtual keyboard and bearer plugins
# pull in. Windows IME goes through qwindows, not platforminputcontexts.
_qt_unused_bins = {
    'opengl32sw.dll', 'd3dcompiler_47.dll', 'libEGL.dll', 'libGLESv2.dll',
    'Qt5Quick.dll', 'Qt5Qml.dll', 'Qt5QmlModels.dll', 'Qt5Network.dll',
}
_qt_unused_dirs = {
    ('PyQt5', 'Qt5', 'translations'),
    ('PyQt5', 'Qt5', 'plugins', 'platforminputcontexts'),
    ('PyQt5', 'Qt5', 'plugins', 'bearer'),
}


def _bundled(entry):
    dest, src, _typecode = entry
    parts = Path(dest).parts
    if parts[0] != 'assets' and src and Path(src).resolve().is_relative_to(_assets_dir):
        # PyInstaller resolves the DLL imports of the .exe files under
        # assets/ and copies them a second time next to EmuCtrl.exe.
        return False
    if parts[:2] == ('assets', 'scrcpy') and parts[-1] not in _scrcpy_keep:
        return False
    if parts[:2] == ('assets', 'mediamtx'):  # legacy local download
        return False
    if parts[:3] == ('PyQt5', 'Qt5', 'bin') and parts[-1] in _qt_unused_bins:
        return False
    return not any(parts[:len(d)] == d for d in _qt_unused_dirs)


a.binaries = [e for e in a.binaries if _bundled(e)]
a.datas = [e for e in a.datas if _bundled(e)]

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='EmuCtrl',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
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
    name='EmuCtrl',
)
