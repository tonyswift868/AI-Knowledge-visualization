$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw 'CPython 3.11 x64 is required.' }
if (-not (Get-Command node -ErrorAction SilentlyContinue)) { throw 'Node.js is required.' }
$venv = Join-Path $Root '.venv'
if (-not (Test-Path $venv)) { & py -3.11 -m venv $venv }
$python = Join-Path $venv 'Scripts\python.exe'
& $python -c "import sys,struct; assert sys.version_info[:2] == (3,11), sys.version; assert struct.calcsize('P')*8 == 64, 'x64 required'"
& $python -m pip install --upgrade pip
& $python -m pip install -r (Join-Path $Root 'backend\requirements.lock.txt') -r (Join-Path $Root 'backend\requirements-desktop.lock.txt')
& $python -c "import PyInstaller,PySide6,win32ctypes; from win32ctypes.pywin32 import pywintypes,win32api; assert PyInstaller.__version__ == '6.21.0'; assert PySide6.__version__ == '6.11.1'; print('Frozen build dependencies PASS')"
& $python -m pip check
& npm.cmd --prefix (Join-Path $Root 'frontend') install
& $python -c "from desktop.backend_imports import prepare_backend_imports; prepare_backend_imports(); from app.db import init_db; init_db(); print('SQLite initialized')"
Push-Location (Join-Path $Root 'backend')
& $python -m alembic upgrade head
if ($LASTEXITCODE -ne 0) { throw "Alembic migration failed: $LASTEXITCODE" }
Pop-Location
Write-Host 'MentorFlow bootstrap complete.' -ForegroundColor Green
& powershell -ExecutionPolicy Bypass -File (Join-Path $Root 'scripts\build_native_desktop.ps1')
