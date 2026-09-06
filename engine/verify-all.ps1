# Windows PowerShell one-command automated verification for WindowControl v3.1.0
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Write-Host "Starting WindowControl v3.1.0 Automated Monorepo Verification..." -ForegroundColor Cyan
& uv run python (Join-Path $repoRoot "scripts\verify_all.py")
exit $LASTEXITCODE
