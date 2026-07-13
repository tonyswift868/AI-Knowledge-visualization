$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw '请先运行 scripts\bootstrap.ps1' }
if (-not (Test-Path (Join-Path $Root 'frontend\dist\index.html'))) { throw '请先运行 scripts\build.ps1' }
Push-Location (Join-Path $Root 'backend')
& $python -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw "Alembic migration failed: $LASTEXITCODE" }
Pop-Location
Write-Host 'MentorFlow listening at http://127.0.0.1:8000' -ForegroundColor Green
& $python -m uvicorn app.main:app --app-dir (Join-Path $Root 'backend') --host 127.0.0.1 --port 8000
