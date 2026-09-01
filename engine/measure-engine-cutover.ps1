# Repeatable five-instance performance comparison. It observes only operator-
# supplied serials and never starts or stops an LDPlayer instance.
[CmdletBinding()]
param(
    [ValidateSet("legacy", "engine")]
    [string]$Mode,
    [ValidateSet("no-viewer", "one-viewer")]
    [string]$Workload,
    [string[]]$Serials,
    [int]$DurationSeconds = 60,
    [double]$SampleIntervalSeconds = 1.0,
    [string]$RecordDecision = "",
    [string[]]$ResultFiles = @(),
    [string]$SubmitManualMetrics = "",
    [switch]$AwaitDecision
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if ($SubmitManualMetrics) {
    & uv run python -m scripts.measure_engine_cutover --repo-root $repoRoot --submit-manual-metrics $SubmitManualMetrics
    exit $LASTEXITCODE
}
if ($RecordDecision) {
    & uv run python -m scripts.measure_engine_cutover --repo-root $repoRoot --record-decision $RecordDecision
    exit $LASTEXITCODE
}
if ($AwaitDecision) {
    if ($ResultFiles.Count -ne 4) { throw "-AwaitDecision requires exactly four -ResultFiles" }
    $decisionDir = Split-Path -Parent $ResultFiles[0]
    $args = @("run", "python", "-m", "scripts.measure_engine_cutover", "--repo-root", $repoRoot,
        "--evidence-dir", $decisionDir, "--await-decision")
    foreach ($result in $ResultFiles) { $args += @("--result-file", $result) }
    & uv @args
    exit $LASTEXITCODE
}

if (-not $Mode -or -not $Workload -or -not $Serials) { throw "-Mode, -Workload, and -Serials are required for a measurement" }
if ($Serials.Count -ne 5 -or @($Serials | Select-Object -Unique).Count -ne 5) {
    throw "-Serials must contain exactly five unique ready devices"
}
if ($DurationSeconds -lt 30) { throw "-DurationSeconds must be at least 30" }
$timestamp = (Get-Date -Format "yyyyMMdd-HHmmss-fff") + "-" + $PID
$evidenceDir = Join-Path $repoRoot ("engine\test\performance-" + $timestamp)
$args = @("run", "python", "-m", "scripts.measure_engine_cutover", "--repo-root", $repoRoot,
    "--mode", $Mode, "--workload", $Workload, "--duration-seconds", "$DurationSeconds",
    "--sample-interval-seconds", "$SampleIntervalSeconds", "--evidence-dir", $evidenceDir)
foreach ($serial in $Serials) { $args += @("--serial", $serial) }
Write-Host "Evidence directory: $evidenceDir"
& uv @args
exit $LASTEXITCODE
