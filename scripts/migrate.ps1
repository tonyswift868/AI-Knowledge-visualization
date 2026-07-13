$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'Run scripts\bootstrap.ps1 first.' }
Push-Location (Join-Path $Root 'backend')
try { & $python -m alembic upgrade head; if ($LASTEXITCODE -ne 0) { throw "Alembic migration failed: $LASTEXITCODE" } } finally { Pop-Location }
