# MentorFlow short Core Gate: isolated backend contracts, complete frontend unit tests,
# build, security smoke, and release privacy checks.
# Full Gate (including Playwright and the remaining slow suites):
#   powershell -ExecutionPolicy Bypass -File scripts\test.ps1

[CmdletBinding()]
param()

$ErrorActionPreference = 'Continue'
$root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$previousLocation = (Get-Location).Path
$python = Join-Path $root '.venv\Scripts\python.exe'
$frontend = Join-Path $root 'frontend'
$tempParent = 'E:\Codex\Temp'
$testTemp = $null
$resolvedTempParent = $null
$resolvedTestTemp = $null
$stepResults = [System.Collections.Generic.List[object]]::new()
$started = [DateTime]::UtcNow

if (-not (Test-Path -LiteralPath $python)) {
    throw "Missing project virtualenv Python: $python (run scripts\bootstrap.ps1 first)"
}
if (-not (Test-Path -LiteralPath (Join-Path $frontend 'package.json'))) {
    throw "Missing frontend package manifest: $(Join-Path $frontend 'package.json')"
}

New-Item -ItemType Directory -Path $tempParent -Force | Out-Null
$testTemp = Join-Path $tempParent ("mentorflow-core-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testTemp -ErrorAction Stop | Out-Null
$resolvedTempParent = (Resolve-Path -LiteralPath $tempParent).Path.TrimEnd('\') + '\'
$resolvedTestTemp = (Resolve-Path -LiteralPath $testTemp).Path
if (-not $resolvedTestTemp.StartsWith($resolvedTempParent, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing unsafe Core Gate temp path: $resolvedTestTemp"
}

$liveDatabases = @(
    (Join-Path $root 'data\app.db'),
    (Join-Path $root 'data\mentorflow_v2.db')
)
$liveHashesBefore = @{}
foreach ($database in $liveDatabases) {
    if (Test-Path -LiteralPath $database) {
        $liveHashesBefore[$database] = (Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash
    }
}

$environmentNames = @(
    'TMP',
    'TEMP',
    'TMPDIR',
    'PYTHONPATH',
    'MENTORFLOW_DB',
    'MENTORFLOW_ORM_DB',
    'MENTORFLOW_ACTIVE_UPLOAD_ROOT',
    'MENTORFLOW_CLASSROOM_ASR_UPLOAD_ROOT'
)
$environmentBefore = @{}
$environmentWasPresent = @{}
foreach ($name in $environmentNames) {
    $entry = Get-Item -Path ("Env:{0}" -f $name) -ErrorAction SilentlyContinue
    $environmentWasPresent[$name] = ($null -ne $entry)
    $environmentBefore[$name] = if ($null -ne $entry) { [string]$entry.Value } else { $null }
}

function Invoke-CoreStep {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][scriptblock]$Action
    )

    $timer = [Diagnostics.Stopwatch]::StartNew()
    Write-Host ("CORE STEP START: {0}" -f $Name) -ForegroundColor Cyan
    $code = 0
    try {
        & $Action
        if ($null -ne $LASTEXITCODE) {
            $code = [int]$LASTEXITCODE
        }
    } catch {
        Write-Host ("CORE STEP ERROR: {0}" -f $_.Exception.Message) -ForegroundColor Red
        $code = 1
    }
    $timer.Stop()
    $seconds = [Math]::Round($timer.Elapsed.TotalSeconds, 2)
    $stepResults.Add([pscustomobject]@{ Name = $Name; ExitCode = $code; Seconds = $seconds }) | Out-Null
    $colour = if ($code -eq 0) { 'Green' } else { 'Red' }
    Write-Host ("CORE STEP END: {0} exit={1} elapsed={2}s" -f $Name, $code, $seconds) -ForegroundColor $colour
}

$isolatedData = Join-Path $testTemp 'data'
$isolatedUploadRoot = Join-Path $isolatedData 'active_teaching\uploads'
$isolatedClassroomRoot = Join-Path $isolatedData 'classroom\uploads'
New-Item -ItemType Directory -Path $isolatedData, $isolatedUploadRoot, $isolatedClassroomRoot -Force | Out-Null

$backendTests = @(
    'backend/tests/test_expedition_profile_concurrency.py',
    'backend/tests/test_safe_xml.py',
    'backend/tests/test_conversion_registry_v1.py',
    'backend/tests/test_provider_url_policy.py',
    'backend/tests/test_resource_references.py',
    'backend/tests/test_planning_resource_authorization.py',
    'backend/tests/test_upload_limits.py',
    'backend/tests/test_database_convergence_cli.py',
    'backend/tests/test_api_contract_startup.py',
    'backend/tests/test_teacher_avatar_card_presets.py',
    'backend/tests/test_knowledge_map_api.py',
    'backend/tests/test_knowledge_map_progress.py',
    'backend/tests/test_knowledge_map_v1_api.py'
)

try {
    Set-Location -LiteralPath $root

    $env:TMP = $testTemp
    $env:TEMP = $testTemp
    $env:TMPDIR = $testTemp
    $env:PYTHONPATH = Join-Path $root 'backend'
    $env:MENTORFLOW_DB = Join-Path $isolatedData 'app.db'
    $env:MENTORFLOW_ORM_DB = Join-Path $isolatedData 'mentorflow_v2.db'
    $env:MENTORFLOW_ACTIVE_UPLOAD_ROOT = $isolatedUploadRoot
    $env:MENTORFLOW_CLASSROOM_ASR_UPLOAD_ROOT = $isolatedClassroomRoot

    Invoke-CoreStep -Name 'isolated ORM schema migration' -Action {
        & $python -c "from app.orm import upgrade_orm_schema; upgrade_orm_schema(); print('Isolated Alembic migration complete')"
    }
    Invoke-CoreStep -Name 'backend critical contracts (expedition, XML/conversion, provider/planning, upload, DB, startup, avatar, knowledge-map)' -Action {
        $pytestArguments = @('-m', 'pytest', '-q', '--import-mode=importlib', '-o', ('cache_dir={0}' -f (Join-Path $testTemp 'pytest-cache'))) + $backendTests
        & $python @pytestArguments
    }
    Invoke-CoreStep -Name 'release privacy tests' -Action {
        & $python -m pytest -q --import-mode=importlib -o ('cache_dir={0}' -f (Join-Path $testTemp 'pytest-cache-release')) backend/tests/test_windows_package_privacy.py
    }
    Invoke-CoreStep -Name 'frontend complete unit tests' -Action {
        & npm.cmd --prefix $frontend run test -- --run
    }
    Invoke-CoreStep -Name 'frontend production build' -Action {
        & npm.cmd --prefix $frontend run build
    }
    Invoke-CoreStep -Name 'security smoke' -Action {
        & $python scripts/security_smoke.py
    }
} finally {
    foreach ($database in $liveHashesBefore.Keys) {
        if (-not (Test-Path -LiteralPath $database)) {
            Write-Host ("CORE INTEGRITY ERROR: live database disappeared: {0}" -f $database) -ForegroundColor Red
            $stepResults.Add([pscustomobject]@{ Name = "live database unchanged ($database)"; ExitCode = 1; Seconds = 0 }) | Out-Null
            continue
        }
        $after = (Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash
        if ($after -ne $liveHashesBefore[$database]) {
            Write-Host ("CORE INTEGRITY ERROR: live database changed: {0}" -f $database) -ForegroundColor Red
            $stepResults.Add([pscustomobject]@{ Name = "live database unchanged ($database)"; ExitCode = 1; Seconds = 0 }) | Out-Null
        }
    }

    foreach ($name in $environmentNames) {
        $path = "Env:{0}" -f $name
        if ($environmentWasPresent[$name]) {
            Set-Item -Path $path -Value $environmentBefore[$name]
        } else {
            Remove-Item -Path $path -ErrorAction SilentlyContinue
        }
    }

    if ($null -ne $testTemp -and (Test-Path -LiteralPath $testTemp)) {
        try {
            $resolvedCleanupTarget = (Resolve-Path -LiteralPath $testTemp).Path
            if (-not $resolvedCleanupTarget.StartsWith($resolvedTempParent, [StringComparison]::OrdinalIgnoreCase)) {
                throw "Refusing unsafe recursive cleanup target: $resolvedCleanupTarget"
            }
            Remove-Item -LiteralPath $resolvedCleanupTarget -Recurse -Force -ErrorAction Stop
            Write-Host ("CORE CLEANUP: removed {0}" -f $resolvedCleanupTarget) -ForegroundColor DarkGray
        } catch {
            Write-Host ("CORE CLEANUP WARNING: retained {0}; {1}" -f $testTemp, $_.Exception.Message) -ForegroundColor Yellow
            $stepResults.Add([pscustomobject]@{ Name = 'isolated temp cleanup'; ExitCode = 1; Seconds = 0 }) | Out-Null
        }
    }

    Set-Location -LiteralPath $previousLocation
}

$elapsed = [Math]::Round(([DateTime]::UtcNow - $started).TotalSeconds, 2)
$failed = @($stepResults | Where-Object { $_.ExitCode -ne 0 })
Write-Host ''
Write-Host 'CORE GATE RESULTS' -ForegroundColor Cyan
foreach ($result in $stepResults) {
    Write-Host ("  {0}: exit={1}, elapsed={2}s" -f $result.Name, $result.ExitCode, $result.Seconds)
}
Write-Host ("CORE GATE TOTAL: {0}s; steps={1}; failed={2}" -f $elapsed, $stepResults.Count, $failed.Count)
if ($failed.Count -gt 0) {
    Write-Host 'CORE GATE FAILED' -ForegroundColor Red
    exit 1
}
Write-Host 'CORE GATE PASSED' -ForegroundColor Green
exit 0
