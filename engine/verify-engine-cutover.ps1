# Windows-only final engine direct-cutover verifier.
# Lifecycle policy lives in the dependency-injected Python state machine.

[CmdletBinding()]
param(
    [string[]]$Serials = @(),
    [string]$PerformanceEvidenceDir = "",
    [string]$PublicSignalingUrl = "",
    [string]$InstallerPath = "",
    [double]$SoakHours = 8,
    [switch]$KeepOnFailure,
    [switch]$FilePrompts,
    [ValidateSet("", "PASS", "FAIL")]
    [string]$Confirm = ""
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

if ($Confirm) {
    & uv run python -m scripts.verify_engine_cutover `
        --repo-root $repoRoot `
        --confirm $Confirm
    exit $LASTEXITCODE
}

if ($Serials.Count -ne 5 -or ($Serials | Select-Object -Unique).Count -ne 5) {
    throw "-Serials must contain exactly five unique ready ADB serials"
}
if (-not $PerformanceEvidenceDir) {
    throw "-PerformanceEvidenceDir is required"
}
if (-not $PublicSignalingUrl) {
    throw "-PublicSignalingUrl is required"
}
if (-not $InstallerPath) {
    $InstallerPath = Join-Path $repoRoot "release\WindowControlInstaller.exe"
}
if (-not [System.IO.Path]::IsPathRooted($PerformanceEvidenceDir)) {
    $PerformanceEvidenceDir = Join-Path $repoRoot $PerformanceEvidenceDir
}
if (-not [System.IO.Path]::IsPathRooted($InstallerPath)) {
    $InstallerPath = Join-Path $repoRoot $InstallerPath
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss-fff"
$nonce = [Guid]::NewGuid().ToString("N").Substring(0, 12)
$evidenceDir = Join-Path $repoRoot "engine\test\engine-cutover-$timestamp-$PID-$nonce"
$recordedOverride = "skip five-instance validation; proceed with engine-only cutover"
$arguments = @(
    "run", "python", "-m", "scripts.verify_engine_cutover",
    "--repo-root", $repoRoot,
    "--serials"
) + $Serials + @(
    "--performance-evidence-dir", $PerformanceEvidenceDir,
    "--evidence-dir", $evidenceDir,
    "--public-signaling-url", $PublicSignalingUrl,
    "--installer-path", $InstallerPath,
    "--soak-hours", "$SoakHours",
    "--performance-override", $recordedOverride
)
if ($KeepOnFailure) { $arguments += "--keep-on-failure" }
if ($FilePrompts) { $arguments += "--file-prompts" }

Write-Host "Evidence directory: $evidenceDir"
Write-Warning "Performance gate is OVERRIDDEN by the recorded owner ruling; it will not be reported as a measured PASS."
& uv @arguments
exit $LASTEXITCODE
