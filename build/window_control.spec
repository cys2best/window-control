# build/window_control.spec
# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

block_cipher = None

_root = Path(SPECPATH).parent
src_dir = str(_root / 'src')

a = Analysis(
    [str(_root / 'src' / 'main.py')],
    pathex=[src_dir],
    binaries=[],
    datas=[
        (str(_root / 'src' / 'client'), 'client'),
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
        'PIL',
        'qrcode',
        'numpy',
        'imageio_ffmpeg',
        'aiortc',
        'aiortc.sdp',
        'aiortc.rtp',
        'aiortc.rtcdtlstransport',
        'aiortc.rtcicetransport',
        'aiortc.rtcpeerconnection',
        'aiohttp',
        'nest_asyncio',
        'av',
        'av.codec',
        'av.video',
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
    name='WindowControl',
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
    name='WindowControl',
)
