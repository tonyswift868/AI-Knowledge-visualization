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

function Write-JsonFile {
    param([string]$Path, [object]$Value)
    $json = $Value | ConvertTo-Json -Depth 12
    [System.IO.File]::WriteAllText($Path, $json + "`n", [System.Text.UTF8Encoding]::new($false))
}

function Get-ProcessTree {
    param([int]$RootProcessId)
    try {
        $all = @(Get-CimInstance Win32_Process | Select-Object ProcessId, ParentProcessId, Name, CommandLine, CreationDate)
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
            Error = $_.Exception.Message
        })
    }
}

function Save-ProcessTree {
    param([string]$Name, [int]$RootProcessId)
    Write-JsonFile -Path (Join-Path $artifacts $Name) -Value ([pscustomobject]@{
        schema = "mentorflow.windows-process-tree.v1"
        captured_at = [DateTimeOffset]::UtcNow.ToString("o")
        root_process_id = $RootProcessId
        processes = @(Get-ProcessTree -RootProcessId $RootProcessId)
    })
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
    Save-ProcessTree -Name "processes-before.json" -RootProcessId $process.Id

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($timeout)
    $nextSnapshot = [DateTimeOffset]::UtcNow.AddMinutes(5)
    while (-not $process.HasExited -and [DateTimeOffset]::UtcNow -lt $deadline) {
        Start-Sleep -Seconds 2
        if ([DateTimeOffset]::UtcNow -ge $nextSnapshot) {
            Save-ProcessTree -Name "processes-periodic.json" -RootProcessId $process.Id
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
        Save-ProcessTree -Name "processes-timeout.json" -RootProcessId $process.Id
        $tree = @(Get-ProcessTree -RootProcessId $process.Id | Sort-Object ProcessId -Descending)
        foreach ($item in $tree) {
            if ([int]$item.ProcessId -eq $PID) { continue }
            Stop-Process -Id ([int]$item.ProcessId) -Force -ErrorAction SilentlyContinue
        }
        if (-not $process.HasExited) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
        $process.WaitForExit(15000) | Out-Null
        $exitCode = 124
    }
    else {
        $process.WaitForExit()
        $exitCode = $process.ExitCode
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
}
finally {
    $rootId = if ($null -ne $process) { $process.Id } else { $PID }
    Save-ProcessTree -Name "processes-after-cleanup.json" -RootProcessId $rootId
    $finishedAt = [DateTimeOffset]::UtcNow
    Write-JsonFile -Path (Join-Path $artifacts "wrapper-result.json") -Value ([pscustomobject]@{
        schema = "mentorflow.windows-pytest-wrapper-result.v1"
        started_at = $startedAt.ToString("o")
        finished_at = $finishedAt.ToString("o")
        elapsed_seconds = [Math]::Round(($finishedAt - $startedAt).TotalSeconds, 3)
        timeout_seconds = $timeout
        timed_out = $timedOut
        stack_dump_completed = $stackDumpCompleted
        exit_code = $exitCode
        process_id = if ($null -ne $process) { $process.Id } else { $null }
        shard_index = $ShardIndex
        shard_count = $ShardCount
        source_sha = [string]$env:MENTORFLOW_DIAGNOSTIC_SOURCE_SHA
        stdout = "pytest-stdout.log"
        stderr = "pytest-stderr.log"
        timeline = "node-timeline.jsonl"
        status = "status.json"
        trace = "faulthandler.log"
    })
}

Write-Host ("MentorFlow Windows pytest diagnostic exit={0} timed_out={1} shard={2}/{3}" -f $exitCode, $timedOut, $ShardIndex, $ShardCount)
exit $exitCode
