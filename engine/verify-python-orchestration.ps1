# One-command Windows entry point. Lifecycle and polling live in the
# dependency-injected Python driver so the workflow is testable on Darwin.
# Run from the repository root: .\engine\verify-python-orchestration.ps1

[CmdletBinding()]
param(
    [string]$Serial = "",
    [string]$Tier = "720",
    [int]$RelayPort = 8443,
    [int]$PagePort = 8090,
    [switch]$SkipBuild,
    [switch]$SkipTests,
    [switch]$SkipExpiry,
    [switch]$KeepOnFailure,
    [switch]$FilePrompts,
    [string]$Confirm = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if ($Confirm) {
    $confirmation = $Confirm.ToUpperInvariant()
    if ($confirmation -notin @("PASS", "FAIL")) {
        throw "-Confirm must be PASS or FAIL"
    }
    $confirmArguments = @(
        "run", "python", "-m", "scripts.verify_python_orchestration",
        "--repo-root", $repoRoot,
        "--confirm", $confirmation
    )
    & uv @confirmArguments
    exit $LASTEXITCODE
}

$timestamp = (Get-Date -Format "yyyyMMdd-HHmmss-fff") + "-" + $PID
$evidenceDir = Join-Path $repoRoot ("engine\test\verification-" + $timestamp)
$engineExe = Join-Path $repoRoot "engine\build\Release\engine.exe"
$arguments = @(
    "run", "python", "-m", "scripts.verify_python_orchestration",
    "--repo-root", $repoRoot,
    "--engine-exe", $engineExe,
    "--evidence-dir", $evidenceDir,
    "--relay-port", "$RelayPort",
    "--page-port", "$PagePort"
)
if ($Serial) { $arguments += @("--serial", $Serial) }
if ($Tier) { $arguments += @("--tier", $Tier) }
if ($SkipBuild) { $arguments += "--skip-build" }
if ($SkipTests) { $arguments += "--skip-tests" }
if ($SkipExpiry) { $arguments += "--skip-expiry" }
if ($KeepOnFailure) { $arguments += "--keep-on-failure" }
if ($FilePrompts) { $arguments += "--file-prompts" }

Write-Host "Evidence directory: $evidenceDir"
& uv @arguments
exit $LASTEXITCODE
