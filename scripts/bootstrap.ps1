$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw 'Python 3.11+ is required.' }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Node.js is required.' }
$venv = Join-Path $Root '.venv'
if (-not (Test-Path $venv)) { & py -3 -m venv $venv }
$python = Join-Path $venv 'Scripts\python.exe'
& $python -m pip install --upgrade pip
& $python -m pip install -r (Join-Path $Root 'backend\requirements-desktop.txt')
& npm.cmd --prefix (Join-Path $Root 'frontend') install
& $python -c "import sys; sys.path.insert(0, 'backend'); from app.db import init_db; init_db(); print('SQLite initialized')"
Push-Location (Join-Path $Root 'backend')
& $python -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw "Alembic migration failed: $LASTEXITCODE" }
Pop-Location
Write-Host 'MentorFlow bootstrap complete.' -ForegroundColor Green
& powershell -ExecutionPolicy Bypass -File (Join-Path $Root 'scripts\build_native_desktop.ps1')
