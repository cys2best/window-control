# Windows-only final engine direct-cutover verifier.
# Lifecycle policy lives in the dependency-injected Python state machine.

[CmdletBinding()]
param(
    [string[]]$Serials = @(),
    [string]$PerformanceEvidenceDir = "",
    [string]$PublicSignalingUrl = "",
    [string]$InstallerPath = "",
    [double]$SoakHours = 8,
    [int]$RapidSwitchCount = 20,
    [switch]$KeepOnFailure,
    [switch]$FilePrompts,
    [ValidateSet("", "PASS", "FAIL")]
    [string]$Confirm = "",
    [switch]$SkipManualGates,
    [switch]$SoakOverride,
    [switch]$SkipPublicMobile
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
if (-not $PublicSignalingUrl -and -not $SkipPublicMobile) {
    throw "-PublicSignalingUrl is required (or pass -SkipPublicMobile to skip the public/mobile gates)"
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
$recordedSoakOverride = "skip 8-hour soak rerun; single-minute decode-stall accepted as known stability gap, all other gates PASS"
$arguments = @(
    "run", "python", "-m", "scripts.verify_engine_cutover",
    "--repo-root", $repoRoot,
    "--serials"
) + $Serials + @(
    "--performance-evidence-dir", $PerformanceEvidenceDir,
    "--evidence-dir", $evidenceDir,
    "--installer-path", $InstallerPath,
    "--soak-hours", "$SoakHours",
    "--rapid-switch-count", "$RapidSwitchCount",
    "--performance-override", $recordedOverride
)
if ($PublicSignalingUrl) { $arguments += @("--public-signaling-url", $PublicSignalingUrl) }
if ($KeepOnFailure) { $arguments += "--keep-on-failure" }
if ($FilePrompts) { $arguments += "--file-prompts" }
if ($SkipManualGates) { $arguments += "--skip-manual-gates" }
if ($SoakOverride) { $arguments += @("--soak-override", $recordedSoakOverride) }
if ($SkipPublicMobile) { $arguments += "--skip-public-mobile" }

Write-Host "Evidence directory: $evidenceDir"
Write-Warning "Performance gate is OVERRIDDEN by the recorded owner ruling; it will not be reported as a measured PASS."
if ($SkipManualGates) {
    Write-Warning "-SkipManualGates is set: operator confirmations are auto-answered. This run can never report PASS and is not acceptance evidence."
}
if ($SoakOverride) {
    Write-Warning "-SoakOverride is set: the 8-hour soak gate is OVERRIDDEN by the recorded owner ruling and will not be reported as a measured PASS."
}
if ($RapidSwitchCount -lt 20) {
    Write-Warning "-RapidSwitchCount $RapidSwitchCount is below the required 20: the rapid-switch gate will report INCOMPLETE, not PASS."
}
if ($SkipPublicMobile) {
    Write-Warning "-SkipPublicMobile is set: the public browser and mobile gates are SKIPPED, not measured. This run can never report PASS and is not full acceptance evidence."
}
& uv @arguments
exit $LASTEXITCODE
