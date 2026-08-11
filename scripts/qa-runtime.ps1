[CmdletBinding()]
param(
    [ValidateSet('Start', 'Stop', 'Status')]
    [string]$Action = 'Status',
    [switch]$Fresh
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($PSVersionTable.PSVersion.Major -lt 7) {
    throw 'MentorFlow QA runtime requires PowerShell 7 or newer.'
}

$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$QaRoot = if ($env:MENTORFLOW_QA_ROOT) {
    [System.IO.Path]::GetFullPath($env:MENTORFLOW_QA_ROOT)
} else {
    Join-Path $env:LOCALAPPDATA 'MentorFlow\qa-runtime'
}
$StatePath = Join-Path $QaRoot 'runtime.json'
$BackendPort = 8011
$FrontendPort = 5174

function Get-PortOwner {
    param([int]$Port)
    Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Get-QaState {
    if (-not (Test-Path -LiteralPath $StatePath)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $StatePath -Raw -Encoding utf8 | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Test-OwnedListener {
    param(
        [int]$ProcessId,
        [int]$Port
    )
    if ($ProcessId -le 0) {
        return $false
    }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if (-not $process) {
        return $false
    }
    $rootToken = [System.IO.Path]::GetFullPath($Root)
    $commandLine = [string]$process.CommandLine
    return $commandLine.Contains($rootToken, [System.StringComparison]::OrdinalIgnoreCase) -and
        $commandLine.Contains([string]$Port, [System.StringComparison]::OrdinalIgnoreCase)
}

function Wait-HttpReady {
    param(
        [string]$Url,
        [int]$TimeoutSeconds = 30
    )
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        try {
            $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 2
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
                return
            }
        } catch {
            Start-Sleep -Milliseconds 250
        }
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "QA service did not become ready: $Url"
}

function Backup-SqliteDatabase {
    param(
        [string]$Source,
        [string]$Destination
    )
    $backupProgram = @'
import pathlib
import sqlite3
import sys

source = pathlib.Path(sys.argv[1]).resolve()
destination = pathlib.Path(sys.argv[2]).resolve()
destination.parent.mkdir(parents=True, exist_ok=True)
if destination.exists():
    destination.unlink()
src = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
dst = sqlite3.connect(destination)
try:
    src.backup(dst)
finally:
    dst.close()
    src.close()
'@
    & $Python -c $backupProgram $Source $Destination
    if ($LASTEXITCODE -ne 0) {
        throw "SQLite backup failed: $Source"
    }
}

function Prepare-QaSnapshot {
    if (-not (Test-Path -LiteralPath $Python)) {
        throw 'Project Python environment is missing. Run scripts\bootstrap.ps1 first.'
    }
    New-Item -ItemType Directory -Path $QaRoot -Force | Out-Null

    $qaData = Join-Path $QaRoot 'data'
    $qaUploads = Join-Path $qaData 'active_teaching\uploads'
    New-Item -ItemType Directory -Path $qaUploads -Force | Out-Null

    $legacySource = Join-Path $Root 'data\app.db'
    $ormSource = Join-Path $Root 'data\mentorflow_v2.db'
    $legacyDestination = Join-Path $qaData 'app.db'
    $ormDestination = Join-Path $qaData 'mentorflow_v2.db'

    Backup-SqliteDatabase -Source $legacySource -Destination $legacyDestination
    Backup-SqliteDatabase -Source $ormSource -Destination $ormDestination

    $sourceUploads = Join-Path $Root 'data\active_teaching\uploads'
    if (Test-Path -LiteralPath $sourceUploads) {
        Get-ChildItem -LiteralPath $sourceUploads -Force |
            Copy-Item -Destination $qaUploads -Recurse -Force
    }

    $sanitizeProgram = @'
import pathlib
import sqlite3
import sys

legacy_db = pathlib.Path(sys.argv[1]).resolve()
orm_db = pathlib.Path(sys.argv[2]).resolve()
source_uploads = str(pathlib.Path(sys.argv[3]).resolve())
qa_uploads = str(pathlib.Path(sys.argv[4]).resolve())

legacy = sqlite3.connect(legacy_db)
try:
    legacy.execute(
        "UPDATE workspaces SET execution_mode='demo', provider_name='fake', provider_model=''"
    )
    legacy.commit()
finally:
    legacy.close()

orm = sqlite3.connect(orm_db)
try:
    orm.execute(
        "UPDATE orm_workspaces SET execution_mode='demo', provider_name='fake', provider_model=''"
    )
    orm.execute(
        "UPDATE active_materials SET stored_path=replace(stored_path, ?, ?)",
        (source_uploads, qa_uploads),
    )
    orm.commit()
finally:
    orm.close()
'@
    & $Python -c $sanitizeProgram $legacyDestination $ormDestination $sourceUploads $qaUploads
    if ($LASTEXITCODE -ne 0) {
        throw 'QA database sanitization failed.'
    }
}

function Show-QaStatus {
    $backend = Get-PortOwner -Port $BackendPort
    $frontend = Get-PortOwner -Port $FrontendPort
    [pscustomobject]@{
        QaRoot = $QaRoot
        BackendUrl = "http://127.0.0.1:$BackendPort"
        BackendPid = if ($backend) { $backend.OwningProcess } else { $null }
        FrontendUrl = "http://127.0.0.1:$FrontendPort"
        FrontendPid = if ($frontend) { $frontend.OwningProcess } else { $null }
        StateFile = $StatePath
    }
}

function Stop-QaRuntime {
    $state = Get-QaState
    if (-not $state) {
        Write-Host 'No owned MentorFlow QA runtime is recorded.' -ForegroundColor Yellow
        return
    }

    $targets = @(
        @{ Name = 'frontend'; Port = $FrontendPort; Pid = [int]$state.frontend_pid },
        @{ Name = 'backend'; Port = $BackendPort; Pid = [int]$state.backend_pid }
    )
    foreach ($target in $targets) {
        if (Test-OwnedListener -ProcessId $target.Pid -Port $target.Port) {
            Stop-Process -Id $target.Pid -Force
            Write-Host "Stopped owned QA $($target.Name) process $($target.Pid)." -ForegroundColor Green
        } elseif (Get-Process -Id $target.Pid -ErrorAction SilentlyContinue) {
            throw "Refusing to stop PID $($target.Pid): ownership validation failed."
        }
    }
    Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
}

function Start-QaRuntime {
    $existingBackend = Get-PortOwner -Port $BackendPort
    $existingFrontend = Get-PortOwner -Port $FrontendPort
    if ($existingBackend -or $existingFrontend) {
        $state = Get-QaState
        if (
            $state -and
            $existingBackend -and
            $existingFrontend -and
            (Test-OwnedListener -ProcessId $existingBackend.OwningProcess -Port $BackendPort) -and
            (Test-OwnedListener -ProcessId $existingFrontend.OwningProcess -Port $FrontendPort)
        ) {
            Show-QaStatus
            return
        }
        throw 'QA port 5174 or 8011 is already occupied by an unowned process.'
    }

    if ($Fresh -or -not (Test-Path -LiteralPath (Join-Path $QaRoot 'data\mentorflow_v2.db'))) {
        Prepare-QaSnapshot
    }

    New-Item -ItemType Directory -Path $QaRoot -Force | Out-Null
    $legacyDb = Join-Path $QaRoot 'data\app.db'
    $ormDb = Join-Path $QaRoot 'data\mentorflow_v2.db'
    $uploadRoot = Join-Path $QaRoot 'data\active_teaching\uploads'

    $childEnvironment = @{
        MENTORFLOW_DB = $legacyDb
        MENTORFLOW_ORM_DB = $ormDb
        MENTORFLOW_ACTIVE_UPLOAD_ROOT = $uploadRoot
        MENTORFLOW_API_TARGET = "http://127.0.0.1:$BackendPort"
    }

    $backend = Start-Process `
        -FilePath $Python `
        -ArgumentList @('-m', 'uvicorn', 'backend.app.main:app', '--host', '127.0.0.1', '--port', [string]$BackendPort) `
        -WorkingDirectory $Root `
        -Environment $childEnvironment `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $QaRoot 'backend.stdout.log') `
        -RedirectStandardError (Join-Path $QaRoot 'backend.stderr.log') `
        -PassThru

    $frontendEnvironment = $childEnvironment.Clone()
    $frontend = Start-Process `
        -FilePath 'npm.cmd' `
        -ArgumentList @('run', 'dev', '--', '--host', '127.0.0.1', '--port', [string]$FrontendPort, '--strictPort') `
        -WorkingDirectory (Join-Path $Root 'frontend') `
        -Environment $frontendEnvironment `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $QaRoot 'frontend.stdout.log') `
        -RedirectStandardError (Join-Path $QaRoot 'frontend.stderr.log') `
        -PassThru

    try {
        Wait-HttpReady -Url "http://127.0.0.1:$BackendPort/api/health"
        Wait-HttpReady -Url "http://127.0.0.1:$FrontendPort"
    } catch {
        foreach ($process in @($frontend, $backend)) {
            Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
        }
        throw
    }

    $backendListener = Get-PortOwner -Port $BackendPort
    $frontendListener = Get-PortOwner -Port $FrontendPort
    if (-not $backendListener -or -not $frontendListener) {
        throw 'QA listener ownership could not be resolved.'
    }

    $state = [ordered]@{
        created_at = [DateTimeOffset]::UtcNow.ToString('o')
        root = $Root
        qa_root = $QaRoot
        backend_pid = [int]$backendListener.OwningProcess
        backend_port = $BackendPort
        frontend_pid = [int]$frontendListener.OwningProcess
        frontend_port = $FrontendPort
        legacy_db = $legacyDb
        orm_db = $ormDb
        upload_root = $uploadRoot
    }
    $state | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $StatePath -Encoding utf8NoBOM
    Show-QaStatus
}

switch ($Action) {
    'Start' { Start-QaRuntime }
    'Stop' { Stop-QaRuntime }
    'Status' { Show-QaStatus }
}
