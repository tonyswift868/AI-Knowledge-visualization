$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $Python)) { throw '请先运行 scripts\bootstrap.ps1' }
Start-Process -FilePath $Python -ArgumentList '-m','desktop.main' -WorkingDirectory $Root -WindowStyle Hidden
