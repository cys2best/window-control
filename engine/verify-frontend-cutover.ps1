# Windows-only frontend/desktop cutover verifier.
# Automates docs/WINDOWS_MANUAL_VALIDATION.md's build/HTTP/process-level
# checks; genuinely manual gates (WebView2 visual confirmation, the
# Supabase two-account browser flow, the leaked-key cross-machine test)
# remain file-prompt confirmations, same mechanism as verify-engine-cutover.ps1.

[CmdletBinding()]
param(
    [string]$WebBuildDir = "",
    [string]$InstallerPath = "",
    [int]$Port = 8080,
    [switch]$KeepOnFailure,
    [switch]$FilePrompts,
    [ValidateSet("", "PASS", "FAIL")]
    [string]$Confirm = "",
    [switch]$SkipManualGates,
    [switch]$SkipInstaller,
    [string]$Only = "",
    [string]$From = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

if ($Only -and $From) {
    throw "-Only and -From are mutually exclusive"
}

if ($Confirm) {
    & uv run python -m scripts.verify_frontend_cutover `
        --repo-root $repoRoot `
        --confirm $Confirm
    exit $LASTEXITCODE
}

if (-not $InstallerPath) {
    $InstallerPath = Join-Path $repoRoot "release\WindowControlInstaller.exe"
}
if (-not $WebBuildDir) {
    $WebBuildDir = Join-Path $repoRoot "apps\web\out"
}
if (-not [System.IO.Path]::IsPathRooted($InstallerPath)) {
    $InstallerPath = Join-Path $repoRoot $InstallerPath
}
if (-not [System.IO.Path]::IsPathRooted($WebBuildDir)) {
    $WebBuildDir = Join-Path $repoRoot $WebBuildDir
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$nonce = [Guid]::NewGuid().ToString("N").Substring(0, 12)
$evidenceDir = Join-Path $repoRoot "engine\test\frontend-cutover-$timestamp-$PID-$nonce"

$arguments = @(
    "run", "python", "-m", "scripts.verify_frontend_cutover",
    "--repo-root", $repoRoot,
    "--evidence-dir", $evidenceDir,
    "--web-build-dir", $WebBuildDir,
    "--installer-path", $InstallerPath,
    "--port", "$Port"
)
if ($KeepOnFailure) { $arguments += "--keep-on-failure" }
if ($FilePrompts) { $arguments += "--file-prompts" }
if ($SkipManualGates) { $arguments += "--skip-manual-gates" }
if ($SkipInstaller) { $arguments += "--skip-installer" }
if ($Only) { $arguments += @("--only", $Only) }
if ($From) { $arguments += @("--from-gate", $From) }

Write-Host "Evidence directory: $evidenceDir"
if ($SkipManualGates) {
    Write-Warning "-SkipManualGates is set: manual gates are auto-answered SKIPPED. This run can never report PASS."
}
if ($SkipInstaller) {
    Write-Warning "-SkipInstaller is set: installer-dependent gates are SKIPPED. This run can never report PASS."
}
if ($Only -or $From) {
    Write-Warning "A partial gate selection (-Only/-From) is set. This run can never report PASS -- use a full run with neither flag for the acceptance record."
}
& uv @arguments
exit $LASTEXITCODE
