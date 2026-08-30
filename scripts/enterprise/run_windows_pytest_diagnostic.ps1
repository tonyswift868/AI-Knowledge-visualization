[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$RepoRoot,

    [Parameter(Mandatory = $true)]
    [string]$ArtifactsDirectory,

    [string]$PythonExecutable = "python",

    [ValidateRange(1, 105)]
    [int]$TimeoutMinutes = 100,

    [ValidateRange(0, 6300)]
    [int]$TimeoutSeconds = 0,

    [ValidateRange(1, 256)]
    [int]$ShardIndex = 1,

    [ValidateRange(1, 256)]
    [int]$ShardCount = 1,

    [switch]$ReturnToCaller,

    [Parameter(Mandatory = $true)]
    [string[]]$PytestArguments
)

$ErrorActionPreference = "Stop"
$repo = (Resolve-Path -LiteralPath $RepoRoot).Path
$artifacts = [System.IO.Path]::GetFullPath($ArtifactsDirectory)
$timeout = if ($TimeoutSeconds -gt 0) { $TimeoutSeconds } else { $TimeoutMinutes * 60 }
$startedAt = [DateTimeOffset]::UtcNow
$process = $null
$timedOut = $false
$stackDumpCompleted = $false
$exitCode = 125
$cleanupAttempted = $false
$cleanupResult = "NOT_REQUIRED"
$cleanupErrors = @()
$finalExitState = "STARTING"
$snapshotIndex = 0

function Write-JsonFile {
    param([string]$Path, [object]$Value)
    $json = $Value | ConvertTo-Json -Depth 12
    [System.IO.File]::WriteAllText($Path, $json + "`n", [System.Text.UTF8Encoding]::new($false))
}

function Get-ProcessTree {
    param([int]$RootProcessId)
    try {
        $all = @(
            Get-CimInstance Win32_Process |
                Select-Object ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine, CreationDate
        )
        $ids = [System.Collections.Generic.HashSet[int]]::new()
        [void]$ids.Add($RootProcessId)
        $changed = $true
        while ($changed) {
            $changed = $false
            foreach ($item in $all) {
                if ($ids.Contains([int]$item.ParentProcessId) -and -not $ids.Contains([int]$item.ProcessId)) {
                    [void]$ids.Add([int]$item.ProcessId)
                    $changed = $true
                }
            }
        }
        return @($all | Where-Object { $ids.Contains([int]$_.ProcessId) } | Sort-Object ProcessId)
    }
    catch {
        return @([pscustomobject]@{
            ProcessId = $RootProcessId
            ParentProcessId = $null
            Name = $null
            ExecutablePath = $null
            CommandLine = $null
            CreationDate = $null
            Error = $_.Exception.Message
        })
    }
}

function Get-PytestPhase {
    $statusPath = Join-Path $artifacts "status.json"
    if (-not (Test-Path -LiteralPath $statusPath)) { return "unknown" }
    try {
        $phase = [string](Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json).current_phase
        if ($phase -in @("setup", "call", "teardown", "process-exit")) { return $phase }
    }
    catch {
        return "unknown"
    }
    return "unknown"
}

function Save-ProcessTree {
    param(
        [string]$Name,
        [int]$RootProcessId,
        [string]$CapturePhase,
        [bool]$CleanupAttempted,
        [string]$CleanupResult,
        [string]$FinalExitState
    )
    $capturedAt = [DateTimeOffset]::UtcNow.ToString("o")
    $pytestPhase = Get-PytestPhase
    $processes = @(
        Get-ProcessTree -RootProcessId $RootProcessId | ForEach-Object {
            [pscustomobject]@{
                pid = if ($null -ne $_.ProcessId) { [int]$_.ProcessId } else { $null }
                ppid = if ($null -ne $_.ParentProcessId) { [int]$_.ParentProcessId } else { $null }
                image_name = [string]$_.Name
                executable_path = [string]$_.ExecutablePath
                full_command_line = [string]$_.CommandLine
                process_creation_time = if ($null -ne $_.CreationDate) { ([DateTimeOffset]$_.CreationDate).ToString("o") } else { $null }
                captured_at = $capturedAt
                capture_phase = $CapturePhase
                pytest_phase = $pytestPhase
                cleanup_attempted = $CleanupAttempted
                cleanup_result = $CleanupResult
                final_exit_state = $FinalExitState
                metadata_error = [string]$_.Error
            }
        }
    )
    Write-JsonFile -Path (Join-Path $artifacts $Name) -Value ([pscustomobject]@{
        schema = "mentorflow.windows-process-tree.v2"
        captured_at = $capturedAt
        capture_phase = $CapturePhase
        pytest_phase = $pytestPhase
        cleanup_attempted = $CleanupAttempted
        cleanup_result = $CleanupResult
        final_exit_state = $FinalExitState
        root_process_id = $RootProcessId
        processes = $processes
    })
}

function Stop-TrackedProcessTree {
    param([int]$RootProcessId)
    $errors = [System.Collections.Generic.List[string]]::new()
    $tree = @(Get-ProcessTree -RootProcessId $RootProcessId | Sort-Object ProcessId -Descending)
    foreach ($item in $tree) {
        if ($null -eq $item.ProcessId -or [int]$item.ProcessId -eq $PID) { continue }
        try {
            Stop-Process -Id ([int]$item.ProcessId) -Force -ErrorAction Stop
        }
        catch {
            if (Get-Process -Id ([int]$item.ProcessId) -ErrorAction SilentlyContinue) {
                $errors.Add("PID $($item.ProcessId): $($_.Exception.Message)")
            }
        }
    }
    Start-Sleep -Milliseconds 500
    $remaining = @(
        Get-ProcessTree -RootProcessId $RootProcessId |
            Where-Object {
                $null -ne $_.ProcessId -and
                (Get-Process -Id ([int]$_.ProcessId) -ErrorAction SilentlyContinue)
            }
    )
    return [pscustomobject]@{
        result = if ($remaining.Count -eq 0) { "COMPLETE" } else { "INCOMPLETE" }
        errors = @($errors)
        remaining_process_ids = @($remaining | ForEach-Object { [int]$_.ProcessId })
    }
}

if (Test-Path -LiteralPath $artifacts) {
    if (@(Get-ChildItem -LiteralPath $artifacts -Force).Count -gt 0) {
        throw "Diagnostic artifact directory must be empty: $artifacts"
    }
}
else {
    [void](New-Item -ItemType Directory -Path $artifacts -Force)
}

$pathSeparator = [System.IO.Path]::PathSeparator
$pluginRoot = Join-Path $repo "scripts\enterprise"
$backendRoot = Join-Path $repo "backend"
$existingPythonPath = [string]$env:PYTHONPATH
$env:PYTHONPATH = @($backendRoot, $pluginRoot, $existingPythonPath) |
    Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
    Join-String -Separator $pathSeparator
$env:PYTHONUNBUFFERED = "1"
$env:MENTORFLOW_WINDOWS_DIAGNOSTIC_DIR = $artifacts
$env:MENTORFLOW_DIAGNOSTIC_STACK_INTERVAL_SECONDS = "300"

$arguments = @(
    "-X", "faulthandler",
    "-m", "pytest",
    "-p", "pytest_windows_diagnostics",
    "-vv",
    "--full-trace",
    "--durations=100",
    "--mentorflow-shard-index", [string]$ShardIndex,
    "--mentorflow-shard-count", [string]$ShardCount
) + $PytestArguments

Write-JsonFile -Path (Join-Path $artifacts "wrapper-start.json") -Value ([pscustomobject]@{
    schema = "mentorflow.windows-pytest-wrapper.v1"
    started_at = $startedAt.ToString("o")
    timeout_seconds = $timeout
    shard_index = $ShardIndex
    shard_count = $ShardCount
    source_sha = [string]$env:MENTORFLOW_DIAGNOSTIC_SOURCE_SHA
    python = $PythonExecutable
    pytest_argument_count = $PytestArguments.Count
    pytest_arguments = @($PytestArguments)
    process_metadata_schema = "mentorflow.windows-process-tree.v2"
    collection_manifest = "collection-manifest.json"
})

try {
    $process = Start-Process `
        -FilePath $PythonExecutable `
        -ArgumentList $arguments `
        -WorkingDirectory $repo `
        -RedirectStandardOutput (Join-Path $artifacts "pytest-stdout.log") `
        -RedirectStandardError (Join-Path $artifacts "pytest-stderr.log") `
        -NoNewWindow `
        -PassThru
    $finalExitState = "RUNNING"
    Save-ProcessTree `
        -Name "processes-before.json" `
        -RootProcessId $process.Id `
        -CapturePhase "process-started" `
        -CleanupAttempted $false `
        -CleanupResult "NOT_ATTEMPTED" `
        -FinalExitState $finalExitState

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($timeout)
    $nextSnapshot = [DateTimeOffset]::UtcNow.AddMinutes(5)
    while (-not $process.HasExited -and [DateTimeOffset]::UtcNow -lt $deadline) {
        Start-Sleep -Seconds 2
        if ([DateTimeOffset]::UtcNow -ge $nextSnapshot) {
            $snapshotIndex += 1
            Save-ProcessTree `
                -Name ("processes-periodic-{0:D3}.json" -f $snapshotIndex) `
                -RootProcessId $process.Id `
                -CapturePhase "periodic-running" `
                -CleanupAttempted $false `
                -CleanupResult "NOT_ATTEMPTED" `
                -FinalExitState $finalExitState
            $nextSnapshot = [DateTimeOffset]::UtcNow.AddMinutes(5)
        }
        $process.Refresh()
    }

    if (-not $process.HasExited) {
        $timedOut = $true
        [System.IO.File]::WriteAllText(
            (Join-Path $artifacts "request-stack-dump"),
            [DateTimeOffset]::UtcNow.ToString("o") + "`n",
            [System.Text.UTF8Encoding]::new($false)
        )
        $stackDeadline = [DateTimeOffset]::UtcNow.AddSeconds(15)
        while ([DateTimeOffset]::UtcNow -lt $stackDeadline) {
            if (Test-Path -LiteralPath (Join-Path $artifacts "stack-dump-complete")) {
                $stackDumpCompleted = $true
                break
            }
            Start-Sleep -Milliseconds 500
        }
        $finalExitState = "TIMEOUT_PENDING_CLEANUP"
        Save-ProcessTree `
            -Name "processes-timeout-before-cleanup.json" `
            -RootProcessId $process.Id `
            -CapturePhase "timeout-before-cleanup" `
            -CleanupAttempted $false `
            -CleanupResult "NOT_ATTEMPTED" `
            -FinalExitState $finalExitState
        $cleanupAttempted = $true
        $cleanup = Stop-TrackedProcessTree -RootProcessId $process.Id
        $cleanupResult = [string]$cleanup.result
        $cleanupErrors = @($cleanup.errors)
        $process.WaitForExit(15000) | Out-Null
        if ($cleanupResult -eq "COMPLETE") {
            $exitCode = 124
            $finalExitState = "TIMED_OUT_CLEANUP_COMPLETE"
        }
        else {
            $exitCode = 125
            $finalExitState = "TIMED_OUT_CLEANUP_INCOMPLETE"
        }
    }
    else {
        $process.WaitForExit()
        $exitCode = $process.ExitCode
        $cleanupResult = "NOT_REQUIRED_PROCESS_EXITED"
        $finalExitState = if ($exitCode -eq 0) { "COMPLETED_SUCCESS" } else { "COMPLETED_FAILURE" }
    }
}
catch {
    Write-JsonFile -Path (Join-Path $artifacts "wrapper-error.json") -Value ([pscustomobject]@{
        schema = "mentorflow.windows-pytest-wrapper-error.v1"
        at = [DateTimeOffset]::UtcNow.ToString("o")
        error_type = $_.Exception.GetType().FullName
        message = $_.Exception.Message
    })
    $exitCode = 125
    $finalExitState = "WRAPPER_ERROR"
    if ($null -ne $process -and -not $process.HasExited) {
        $cleanupAttempted = $true
        $cleanup = Stop-TrackedProcessTree -RootProcessId $process.Id
        $cleanupResult = [string]$cleanup.result
        $cleanupErrors = @($cleanup.errors)
        if ($cleanupResult -ne "COMPLETE") {
            $finalExitState = "WRAPPER_ERROR_CLEANUP_INCOMPLETE"
        }
    }
}
finally {
    $rootId = if ($null -ne $process) { $process.Id } else { $PID }
    Save-ProcessTree `
        -Name "processes-after-cleanup.json" `
        -RootProcessId $rootId `
        -CapturePhase "final-after-cleanup" `
        -CleanupAttempted $cleanupAttempted `
        -CleanupResult $cleanupResult `
        -FinalExitState $finalExitState
    $finishedAt = [DateTimeOffset]::UtcNow
    Write-JsonFile -Path (Join-Path $artifacts "wrapper-result.json") -Value ([pscustomobject]@{
        schema = "mentorflow.windows-pytest-wrapper-result.v2"
        started_at = $startedAt.ToString("o")
        finished_at = $finishedAt.ToString("o")
        elapsed_seconds = [Math]::Round(($finishedAt - $startedAt).TotalSeconds, 3)
        timeout_seconds = $timeout
        timed_out = $timedOut
        stack_dump_completed = $stackDumpCompleted
        exit_code = $exitCode
        final_exit_state = $finalExitState
        cleanup_attempted = $cleanupAttempted
        cleanup_result = $cleanupResult
        cleanup_errors = $cleanupErrors
        process_id = if ($null -ne $process) { $process.Id } else { $null }
        shard_index = $ShardIndex
        shard_count = $ShardCount
        source_sha = [string]$env:MENTORFLOW_DIAGNOSTIC_SOURCE_SHA
        pytest_arguments = @($PytestArguments)
        stdout = "pytest-stdout.log"
        stderr = "pytest-stderr.log"
        timeline = "node-timeline.jsonl"
        status = "status.json"
        trace = "faulthandler.log"
        collection_manifest = "collection-manifest.json"
        process_snapshots = @(
            Get-ChildItem -LiteralPath $artifacts -Filter "processes-*.json" |
                Sort-Object Name |
                ForEach-Object { $_.Name }
        )
    })
}

Write-Host ("MentorFlow Windows pytest diagnostic exit={0} timed_out={1} shard={2}/{3}" -f $exitCode, $timedOut, $ShardIndex, $ShardCount)
$global:LASTEXITCODE = $exitCode
if ($ReturnToCaller) { return }
exit $exitCode
