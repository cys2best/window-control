@echo off
setlocal

echo [WindowControl Build] Starting PyInstaller build...

REM Activate venv
call ..\.venv\Scripts\activate.bat

REM Ensure imageio-ffmpeg is installed (ships ffmpeg.exe, no manual install needed)
python -m pip install imageio-ffmpeg --quiet

REM Run PyInstaller from build/ directory
cd /d "%~dp0"
pyinstaller window_control.spec --distpath ..\dist --workpath ..\build\work --noconfirm

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] PyInstaller failed.
    exit /b 1
)

echo [WindowControl Build] Built at dist\WindowControl\ (one-dir mode)
