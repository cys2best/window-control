# engine/test.ps1
# Rebuild engine.exe and run it against the live scrcpy forward + WHEP.
# Usage (from repo root, in a shell with cmake/cl on PATH):
#   .\engine\test.ps1
#   .\engine\test.ps1 -Serial emulator-5556 -Port 27184 -Scid 2
#   .\engine\test.ps1 -SkipStartServer   # if scrcpy-server is already fresh

param(
    [string]$Serial = "emulator-5554",
    [int]$Port = 27183,
    [int]$Scid = 1,
    [string]$Tier = "720",
    [string]$InstanceName = "poc-instance",
    [switch]$SkipStartServer,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

if (-not $SkipBuild) {
    Write-Host "[test.ps1] building engine.exe (Release)..." -ForegroundColor Cyan
    $buildTime = Measure-Command {
        cmake --build "$repoRoot\engine\build" --config Release
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[test.ps1] build failed, aborting." -ForegroundColor Red
        exit 1
    }
    Write-Host "[test.ps1] build took $($buildTime.TotalSeconds) s" -ForegroundColor Cyan
}

if (-not $SkipStartServer) {
    Write-Host "[test.ps1] starting fresh scrcpy-server on $Serial (port $Port, scid $Scid)..." -ForegroundColor Cyan
    Push-Location $repoRoot
    $env:PYTHONPATH = "src"
    uv run python -c @"
from server.scrcpy_session import _start_server
from server.adb_manager import _find_adb
adb = _find_adb()
ok = _start_server(adb, '$Serial', $Port, scid=$Scid, tier='$Tier')
print('start_server ok=' + str(ok))
"@
    Pop-Location
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[test.ps1] _start_server failed, aborting." -ForegroundColor Red
        exit 1
    }
    # engine.exe must connect while the server is fresh — see the freshness
    # note in engine/test/README_e2e.md; no deliberate delay here.
}

$exe = "$repoRoot\engine\build\Release\engine.exe"
if (-not (Test-Path $exe)) {
    Write-Host "[test.ps1] $exe not found. Run without -SkipBuild first." -ForegroundColor Red
    exit 1
}

Write-Host "[test.ps1] launching engine.exe..." -ForegroundColor Cyan
Write-Host "[test.ps1] Once the ready record JSON prints to stdout, extract whep_port from it." -ForegroundColor Yellow
Write-Host "[test.ps1] Then open test_page.html in a browser:" -ForegroundColor Yellow
Write-Host "[test.ps1] http://localhost:<whep_port>/test_page.html?whep=http://localhost:<whep_port>/whep" -ForegroundColor Yellow

& $exe $InstanceName $Port
