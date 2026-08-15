$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw '请先运行 scripts\bootstrap.ps1' }
$testTempRoot = 'E:\Codex\Temp'
New-Item -ItemType Directory -Path $testTempRoot -Force | Out-Null
$testTemp = Join-Path $testTempRoot ("mentorflow-test-run-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testTemp -ErrorAction Stop | Out-Null
$resolvedTempRoot = (Resolve-Path -LiteralPath $testTempRoot).Path.TrimEnd('\') + '\'
$resolvedTestTemp = (Resolve-Path -LiteralPath $testTemp).Path
if (-not $resolvedTestTemp.StartsWith($resolvedTempRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing unsafe test temp path: $resolvedTestTemp"
}
$liveDatabases = @(
    (Join-Path $Root 'data\app.db'),
    (Join-Path $Root 'data\mentorflow_v2.db')
)
$liveHashesBefore = @{}
foreach ($database in $liveDatabases) {
    if (Test-Path -LiteralPath $database) {
        $liveHashesBefore[$database] = (Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash
    }
}
$env:TMP = $testTemp
$env:TEMP = $testTemp
$env:TMPDIR = $testTemp
$env:PYTHONPATH = Join-Path $Root 'backend'
$env:MENTORFLOW_DB = Join-Path $testTemp 'data\app.db'
$env:MENTORFLOW_ORM_DB = Join-Path $testTemp 'data\mentorflow_v2.db'
$env:MENTORFLOW_ACTIVE_UPLOAD_ROOT = Join-Path $testTemp 'data\active_teaching\uploads'
New-Item -ItemType Directory -Path (Join-Path $testTemp 'data') -Force | Out-Null
$codes=@()
try {
    & $python -c "from app.orm import upgrade_orm_schema; upgrade_orm_schema(); print('Isolated Alembic migration complete')"; $codes += $LASTEXITCODE
    & $python -m pytest backend/tests -q --import-mode=importlib -o "cache_dir=$testTemp\pytest-cache"; $codes += $LASTEXITCODE
    & npm.cmd --prefix (Join-Path $Root 'frontend') run test -- --run; $codes += $LASTEXITCODE
    & npm.cmd --prefix (Join-Path $Root 'frontend') run build; $codes += $LASTEXITCODE
    Push-Location (Join-Path $Root 'frontend')
    try {
        & npm.cmd exec -- playwright test --config=playwright.config.ts
        $codes += $LASTEXITCODE
    } finally {
        Pop-Location
    }
    & $python scripts/smoke_test.py; $codes += $LASTEXITCODE
    & $python scripts/security_smoke.py; $codes += $LASTEXITCODE
} finally {
    foreach ($database in $liveHashesBefore.Keys) {
        if (-not (Test-Path -LiteralPath $database)) {
            Write-Host "Live database disappeared during validation: $database" -ForegroundColor Red
            $codes += 1
            continue
        }
        $after = (Get-FileHash -LiteralPath $database -Algorithm SHA256).Hash
        if ($after -ne $liveHashesBefore[$database]) {
            Write-Host "Live database changed during isolated validation: $database" -ForegroundColor Red
            $codes += 1
        }
    }
    if (Test-Path -LiteralPath $testTemp) {
        $resolvedCleanupTarget = (Resolve-Path -LiteralPath $testTemp).Path
        if (-not $resolvedCleanupTarget.StartsWith($resolvedTempRoot, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing unsafe recursive cleanup target: $resolvedCleanupTarget"
        }
        Remove-Item -LiteralPath $resolvedCleanupTarget -Recurse -Force -ErrorAction SilentlyContinue
    }
}
if ($codes | Where-Object { $_ -ne 0 }) { Write-Host ("Validation failed: " + ($codes -join ',')) -ForegroundColor Red; exit 1 }
Write-Host ("Backend/frontend/build/e2e/smoke/security passed: " + ($codes -join ',')) -ForegroundColor Green
