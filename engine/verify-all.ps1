# Windows PowerShell one-command automated verification for WindowControl v3.1.0
$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

# Load .env if present in repo root
$envFile = Join-Path $repoRoot ".env"
if (Test-Path $envFile) {
    Write-Host "Loading environment variables from $envFile..." -ForegroundColor Gray
    Get-Content $envFile | Where-Object { $_ -match '^\s*([^#][^=]*?)\s*=\s*(.*)$' } | ForEach-Object {
        $name = $Matches[1].Trim()
        $val = $Matches[2].Trim().Trim('"').Trim("'")
        if (-not [string]::IsNullOrEmpty($name)) {
            [System.Environment]::SetEnvironmentVariable($name, $val, "Process")
        }
    }
}

Write-Host "Starting WindowControl v3.1.0 Automated Monorepo Verification..." -ForegroundColor Cyan
& uv run python (Join-Path $repoRoot "scripts\verify_all.py")
exit $LASTEXITCODE
