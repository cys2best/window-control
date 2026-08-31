# One-command Windows verification for the Python engine orchestration path.
#
# Run from the repository root:
#   .\engine\verify-python-orchestration.ps1
#
# The runner owns the build/test setup, local relay, WindowControl app, static
# verifier page, evidence log, and cleanup. The eight acceptance checkpoints
# remain interactive where a human must inspect video, WebRTC stats, or device
# behavior. A PASS/FAIL response is recorded for each checkpoint.

[CmdletBinding()]
param(
    [string]$Serial = "",
    [int]$InstanceIndex = 0,
    [string]$Tier = "720",
    [int]$RelayPort = 8443,
    [int]$PagePort = 8090,
    [switch]$SkipBuild,
    [switch]$SkipTests,
    [switch]$KeepLogs,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$script:childProcesses = New-Object System.Collections.ArrayList
$script:transcriptStarted = $false
$script:verificationFailed = $false
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$evidenceDir = Join-Path $repoRoot ("engine\test\verification-" + $timestamp)
$engineLog = Join-Path $evidenceDir "engine.stdout.log"
$appLog = Join-Path $evidenceDir "window-control.stdout.log"
$relayLog = Join-Path $evidenceDir "signaling-relay.stdout.log"
$script:adb = $null
$script:serialForCleanup = $null
$script:scrcpyPort = 27183 + $InstanceIndex
$script:scid = $InstanceIndex

function Write-Evidence([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "s"), $Message
    Write-Host $line
    if ($script:transcriptStarted) { Add-Content -LiteralPath (Join-Path $evidenceDir "verification.log") -Value $line }
}

function Invoke-Checked([string]$FilePath, [string[]]$Arguments, [string]$Label) {
    Write-Evidence ("running {0}: {1} {2}" -f $Label, $FilePath, ($Arguments -join " "))
    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Label failed with exit code $LASTEXITCODE" }
}

function Start-LoggedProcess([string]$FilePath, [string[]]$Arguments, [string]$StdoutPath, [string]$Label) {
    $argumentText = ($Arguments | ForEach-Object {
        if ($_ -match '[\s"]') { '"' + ($_.Replace('"', '\"')) + '"' } else { $_ }
    }) -join ' '
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $psi.Arguments = $argumentText
    $psi.WorkingDirectory = $repoRoot
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $psi
    $process.Start() | Out-Null
    $process.add_OutputDataReceived({ param($sender, $event) if ($null -ne $event.Data) { Add-Content -LiteralPath $StdoutPath -Value $event.Data } })
    $process.add_ErrorDataReceived({ param($sender, $event) if ($null -ne $event.Data) { Add-Content -LiteralPath $StdoutPath -Value ("[stderr] " + $event.Data) } })
    $process.BeginOutputReadLine()
    $process.BeginErrorReadLine()
    [void]$script:childProcesses.Add($process)
    Write-Evidence ("started {0} pid={1}" -f $Label, $process.Id)
    return $process
}

function Register-EngineCleanup {
    Write-Evidence "cleanup: stopping verifier child processes and removing this instance's ADB forward"
    foreach ($process in @($script:childProcesses)) {
        if ($null -ne $process -and -not $process.HasExited) {
            try { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue } catch { }
            try { $process.WaitForExit(5000) } catch { }
        }
    }
    # uv/python can leave engine.exe alive when the GUI process is terminated;
    # match only this run's instance command line, never every engine process.
    if ($instance) {
        foreach ($engineProcess in @(Get-CimInstance Win32_Process -Filter "Name = 'engine.exe'" | Where-Object { $_.CommandLine -match [regex]::Escape($instance.name) })) {
            try { Stop-Process -Id ([int]$engineProcess.ProcessId) -Force -ErrorAction SilentlyContinue } catch { }
        }
    }
    if ($script:adb -and $script:serialForCleanup) {
        try { & $script:adb -s $script:serialForCleanup shell "pkill -f 'scrcpy-server.*scid=$($script:scid.ToString('x'))'" | Out-Null } catch { }
        try { & $script:adb -s $script:serialForCleanup forward --remove ("tcp:{0}" -f $script:scrcpyPort) | Out-Null } catch { }
        try { & $script:adb -s $script:serialForCleanup forward --list | Out-File (Join-Path $evidenceDir "adb-forward-final.txt") } catch { }
    }
    if (-not $KeepLogs) {
        Write-Evidence ("evidence retained at {0}; child logs are always retained for auditability" -f $evidenceDir)
    }
}

function Ask-Checkpoint([int]$Number, [string]$Description) {
    Write-Host ""
    Write-Host ("CHECKPOINT {0}: {1}" -f $Number, $Description) -ForegroundColor Cyan
    $answer = Read-Host "Type PASS to continue, or FAIL to stop and collect evidence"
    Write-Evidence ("checkpoint {0}: {1} ({2})" -f $Number, $answer, $Description)
    if ($answer -cne "PASS") {
        $script:verificationFailed = $true
        throw "Checkpoint $Number was not marked PASS."
    }
}

try {
    if ($env:OS -ne "Windows_NT") { throw "This verifier must run on the Windows Host PC; Windows integration is not verified on this host." }
    if (-not (Test-Path -LiteralPath (Join-Path $repoRoot "pyproject.toml"))) { throw "Run this command from the window-control repository." }
    New-Item -ItemType Directory -Path $evidenceDir -Force | Out-Null
    New-Item -ItemType File -Path (Join-Path $evidenceDir "verification.log") -Force | Out-Null
    $script:transcriptStarted = $true
    Write-Evidence "one-command Python orchestration verification started"

    $engineExe = Join-Path $repoRoot "engine\build\Release\engine.exe"
    if (-not $SkipBuild) {
        Invoke-Checked "cmake" @("--build", (Join-Path $repoRoot "engine\build"), "--config", "Release") "engine build"
    }
    if (-not (Test-Path -LiteralPath $engineExe)) { throw "Missing $engineExe. Configure/build engine first or omit -SkipBuild only when it exists." }

    if (-not $SkipTests) {
        $phaseTests = @(
            "tests/test_scrcpy_server.py", "tests/test_engine_process.py",
            "tests/test_engine_auth.py", "tests/test_engine_admin.py",
            "tests/test_engine_runtime.py", "tests/test_engine_orchestrator.py",
            "tests/test_scrcpy_session.py", "tests/test_instance_manager.py",
            "tests/test_app.py", "tests/test_main.py", "tests/test_windows_verifier.py"
        )
        Invoke-Checked "uv" (@("run", "pytest") + $phaseTests + @("-v")) "phase-specific Python tests"
        $engineTests = Join-Path $repoRoot "engine\build\Release\engine_tests.exe"
        if (Test-Path -LiteralPath $engineTests) {
            Invoke-Checked $engineTests @("--gtest_filter=-SignalingClient.*:PublicSignalingBridge.*") "offline engine tests"
        }
    }

    $script:adb = (Get-Command adb -ErrorAction SilentlyContinue).Source
    if (-not $script:adb) { throw "adb was not found on PATH." }
    $devices = & $script:adb devices | Where-Object { $_ -match '^([^\s]+)\s+device\s*$' }
    if (-not $Serial) {
        $candidate = @($devices | ForEach-Object { ($_ -split '\s+')[0] })
        if ($candidate.Count -ne 1) { throw "Pass -Serial: expected exactly one ready ADB device, found $($candidate.Count)." }
        $Serial = $candidate[0]
    }
    if (-not (@($devices | ForEach-Object { ($_ -split '\s+')[0] }) -contains $Serial)) { throw "ADB serial '$Serial' is not in device state." }
    $script:serialForCleanup = $Serial
    & $script:adb -s $Serial get-state | Out-File (Join-Path $evidenceDir "adb-state.txt")
    & $script:adb -s $Serial forward --list | Out-File (Join-Path $evidenceDir "adb-forward-before.txt")
    Write-Evidence "ADB device selected: $Serial (instance index $InstanceIndex, scrcpy port $script:scrcpyPort)"

    $whepSecret = [guid]::NewGuid().ToString("N") + [guid]::NewGuid().ToString("N")
    $signalingSecret = [guid]::NewGuid().ToString("N")
    $savedEnvironment = @{}
    foreach ($name in @("ENGINE_EXE_PATH", "ENGINE_WHEP_CAPABILITY_SECRET", "ENGINE_SIGNALING_SECRET", "VPS_SIGNALING_URL", "AUTH_TOKEN", "PUBLIC_UI_URL", "TUNNEL_SECRET")) {
        $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
    }
    $env:ENGINE_EXE_PATH = $engineExe
    $env:ENGINE_WHEP_CAPABILITY_SECRET = $whepSecret
    $env:ENGINE_SIGNALING_SECRET = $signalingSecret
    $env:VPS_SIGNALING_URL = "ws://127.0.0.1:$RelayPort"
    Remove-Item Env:AUTH_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:PUBLIC_UI_URL -ErrorAction SilentlyContinue
    Remove-Item Env:TUNNEL_SECRET -ErrorAction SilentlyContinue

    $relay = Start-LoggedProcess "uv" @("run", "python", "engine\test\local_signaling_server.py", "--host", "127.0.0.1", "--port", "$RelayPort") $relayLog "local signaling relay"
    Start-Sleep -Milliseconds 500
    $app = Start-LoggedProcess "uv" @("run", "python", "src\main.py") $appLog "WindowControl app"
    $baseUrl = "http://127.0.0.1:8080"
    $instances = $null
    for ($attempt = 0; $attempt -lt 90; $attempt++) {
        try { $instances = Invoke-RestMethod "$baseUrl/instances"; if (@($instances | Where-Object { $_.serial -eq $Serial }).Count -gt 0) { break } } catch { }
        Start-Sleep -Seconds 1
    }
    $instance = @($instances | Where-Object { $_.serial -eq $Serial }) | Select-Object -First 1
    if (-not $instance) { throw "WindowControl did not discover serial '$Serial'. See $appLog." }
    $engineProcessCount = @(Get-CimInstance Win32_Process -Filter "Name = 'engine.exe'" | Where-Object { $_.CommandLine -match [regex]::Escape($instance.name) }).Count
    if ($engineProcessCount -ne 1) { throw "Discovery starts exactly one engine.exe: observed $engineProcessCount for $($instance.name)." }
    Write-Evidence "Discovery starts exactly one engine.exe for $($instance.name)"

    $selection = Invoke-RestMethod -Method Post -Uri "$baseUrl/instances/$Serial/engine-select" -ContentType "application/json" -Body "{}"
    $selection | ConvertTo-Json -Depth 6 | Out-File (Join-Path $evidenceDir "selection-initial.json")
    $whepUri = [Uri]$selection.whep_url
    if (-not $selection.whep_token) { throw "Selection did not return a WHEP capability token." }
    Write-Evidence "non-loopback WHEP URL candidate: $($selection.whep_url)"
    Write-Evidence "fresh selection token is present; the verifier page can repeat selection after expiry"
    Ask-Checkpoint 1 "Discovery starts exactly one engine.exe before selection."
    Ask-Checkpoint 2 "The engine ready record publishes a non-loopback WHEP URL through /engine-select, and the token-aware page can POST WHEP."

    $pageUrl = "http://127.0.0.1:$PagePort/python_orchestration_verifier.html?app=$([uri]::EscapeDataString($baseUrl))&serial=$([uri]::EscapeDataString($Serial))"
    $pageServer = Start-LoggedProcess "uv" @("run", "python", "-m", "http.server", "$PagePort", "--directory", "engine\test") (Join-Path $evidenceDir "verifier-page.stdout.log") "verifier page server"
    if (-not $NoBrowser) { Start-Process $pageUrl }
    Write-Evidence "verifier page: $pageUrl"
    Ask-Checkpoint 3 "A second selection after the first token's expiry returns a fresh token and still negotiates (use Refresh selection on the page after expiry)."
    Ask-Checkpoint 4 "quality/reconnect advances generation while the existing peer remains connected and renders the new dimensions."

    Write-Host "The next checkpoints intentionally exercise recovery on the selected device." -ForegroundColor Yellow
    Ask-Checkpoint 5 "scrcpy-server death produces stalled/disconnected health and watchdog recovery without replacing the engine process or WHEP port."
    Ask-Checkpoint 6 "engine.exe death causes one scrcpy relaunch and one engine respawn; /engine-select returns the new dynamic WHEP port."
    Ask-Checkpoint 7 "emulator removal during recovery leaves no engine process and no ADB forward for this instance."
    Ask-Checkpoint 8 "application exit leaves no engine processes or instance forwards."

    Write-Evidence "all eight Windows matrix checkpoints marked PASS by operator"
    Write-Host "PASS: evidence is in $evidenceDir" -ForegroundColor Green
}
catch {
    Write-Evidence ("verification stopped: " + $_.Exception.Message)
    Write-Host "FAIL: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
finally {
    Register-EngineCleanup
    if ($savedEnvironment) {
        foreach ($name in $savedEnvironment.Keys) {
            if ($null -eq $savedEnvironment[$name]) { Remove-Item ("Env:{0}" -f $name) -ErrorAction SilentlyContinue }
            else { Set-Item ("Env:{0}" -f $name) $savedEnvironment[$name] }
        }
    }
}
