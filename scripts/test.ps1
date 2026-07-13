$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw '请先运行 scripts\bootstrap.ps1' }
$env:PYTHONPATH = Join-Path $Root 'backend'
$codes=@()
& powershell.exe -ExecutionPolicy Bypass -File (Join-Path $Root 'scripts\migrate.ps1'); $codes += $LASTEXITCODE
& $python -m pytest backend/tests -q --import-mode=importlib; $codes += $LASTEXITCODE
& npm.cmd --prefix (Join-Path $Root 'frontend') run test -- --run; $codes += $LASTEXITCODE
& npm.cmd --prefix (Join-Path $Root 'frontend') run build; $codes += $LASTEXITCODE
Push-Location (Join-Path $Root 'frontend')
& npm.cmd exec -- playwright test --config=playwright.config.ts
$codes += $LASTEXITCODE
Pop-Location
& $python scripts/smoke_test.py; $codes += $LASTEXITCODE
& $python scripts/security_smoke.py; $codes += $LASTEXITCODE
if ($codes | Where-Object { $_ -ne 0 }) { Write-Host ("Validation failed: " + ($codes -join ',')) -ForegroundColor Red; exit 1 }
Write-Host ("Backend/frontend/build/e2e/smoke/security passed: " + ($codes -join ',')) -ForegroundColor Green
