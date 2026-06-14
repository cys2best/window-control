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

REM Compile installer
%ISCC% "%~dp0installer.iss"
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Inno Setup compilation failed.
    exit /b 1
)

echo [WindowControl Build] Installer built at release\WindowControlInstaller.exe
