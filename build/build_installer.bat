@echo off
setlocal

echo [WindowControl Build] Building installer...

REM First build the EXE
call "%~dp0build.bat"
if %ERRORLEVEL% NEQ 0 exit /b 1

REM Find Inno Setup compiler
set ISCC="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist %ISCC% (
    echo [ERROR] Inno Setup 6 not found at %ISCC%
    echo Install from: https://jrsoftware.org/isdl.php
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
