@echo off
setlocal

echo [WindowControl Build] Building installer...

REM First build the EXE (unless --no-build or -n is passed)
if /i "%~1"=="--no-build" goto find_iscc
if /i "%~1"=="-n" goto find_iscc
call "%~dp0build.bat"
if %ERRORLEVEL% NEQ 0 exit /b 1

:find_iscc
REM Find Inno Setup compiler
set ISCC=
where iscc.exe >nul 2>&1 && for /f "delims=" %%I in ('where iscc.exe') do set ISCC="%%I"
if not defined ISCC if exist "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" set ISCC="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "C:\Program Files\Inno Setup 6\ISCC.exe" set ISCC="C:\Program Files\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe" set ISCC="%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe"

if not defined ISCC (
    echo [ERROR] Inno Setup 6 not found.
    echo Install via winget: winget install JRSoftware.InnoSetup
    echo Or download from: https://jrsoftware.org/isdl.php
    exit /b 1
)

REM Download the VC++ runtime installer.iss bundles, if not already staged.
if not exist "%~dp0vc_redist.x64.exe" (
    echo [WindowControl Build] Downloading vc_redist.x64.exe...
    curl -L -o "%~dp0vc_redist.x64.exe" "https://aka.ms/vs/17/release/vc_redist.x64.exe"
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] failed to download vc_redist.x64.exe.
        exit /b 1
    )
)

REM Compile installer
%ISCC% "%~dp0installer.iss"
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Inno Setup compilation failed.
    exit /b 1
)

echo [WindowControl Build] Installer built at release\WindowControlInstaller.exe
