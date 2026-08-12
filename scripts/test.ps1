$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw '请先运行 scripts\bootstrap.ps1' }
$pwsh = (Get-Command pwsh.exe -ErrorAction Stop).Source
$testTemp = Join-Path $Root 'tmp\test-run'
New-Item -ItemType Directory -Path $testTemp -Force | Out-Null
$env:TMP = $testTemp
$env:TEMP = $testTemp
$env:TMPDIR = $testTemp
$env:PYTHONPATH = Join-Path $Root 'backend'
$codes=@()
try {
    & $pwsh -NoLogo -NoProfile -NonInteractive -File (Join-Path $Root 'scripts\migrate.ps1'); $codes += $LASTEXITCODE
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
    if (Test-Path -LiteralPath $testTemp) {
        Remove-Item -LiteralPath $testTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
if ($codes | Where-Object { $_ -ne 0 }) { Write-Host ("Validation failed: " + ($codes -join ',')) -ForegroundColor Red; exit 1 }
Write-Host ("Backend/frontend/build/e2e/smoke/security passed: " + ($codes -join ',')) -ForegroundColor Green
