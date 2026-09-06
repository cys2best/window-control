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
