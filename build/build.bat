@echo off
setlocal

echo [WindowControl Build] Starting PyInstaller build...

REM Activate venv
call ..\.venv\Scripts\activate.bat

REM Build the engine (Release) so engine.exe + DLLs can be staged below.
cd /d "%~dp0.."
cmake --build engine\build --config Release
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] cmake --build engine\build --config Release failed.
    exit /b 1
)

REM Stage engine.exe + runtime DLLs into src\assets\engine, replacing any
REM stale copies from a previous build.
if exist src\assets\engine rmdir /s /q src\assets\engine
mkdir src\assets\engine
copy /Y engine\build\Release\engine.exe src\assets\engine\
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] failed to copy engine.exe into src\assets\engine.
    exit /b 1
)
copy /Y engine\build\Release\*.dll src\assets\engine\

REM A dirty checkout may still have the obsolete mediamtx directory from an
REM earlier legacy build; remove it so PyInstaller cannot repackage it.
if exist src\assets\mediamtx rmdir /s /q src\assets\mediamtx

REM Download scrcpy binaries if missing (mediamtx is no longer downloaded).
python scripts\download_assets.py
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] download_assets.py failed.
    exit /b 1
)

REM Build apps/web's static export (Next.js, output: "export") so
REM window_control.spec has an apps\web\out to stage as this build's
REM served UI (replaces the old src\client, which was committed source and
REM needed no build step -- apps/web is a build artifact instead).
call npm install
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] npm install failed.
    exit /b 1
)
call npm run build -w apps/web
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] npm run build -w apps/web failed.
    exit /b 1
)

REM Run PyInstaller from build/ directory
cd /d "%~dp0"
pyinstaller window_control.spec --distpath ..\dist --workpath ..\build\work --noconfirm

if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] PyInstaller failed.
    exit /b 1
)

echo [WindowControl Build] Built at dist\WindowControl\ (one-dir mode)
