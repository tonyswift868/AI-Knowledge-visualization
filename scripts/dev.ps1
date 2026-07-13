$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw '请先运行 scripts\bootstrap.ps1' }
$backend = Start-Process -FilePath $python -ArgumentList '-m','uvicorn','app.main:app','--reload','--host','127.0.0.1','--port','8000' -WorkingDirectory (Join-Path $Root 'backend') -PassThru
$frontend = Start-Process -FilePath 'npm.cmd' -ArgumentList 'run','dev' -WorkingDirectory (Join-Path $Root 'frontend') -PassThru
Write-Host "Backend PID: $($backend.Id)  http://127.0.0.1:8000" -ForegroundColor Green
Write-Host "Frontend PID: $($frontend.Id) http://127.0.0.1:5173" -ForegroundColor Green
function Stop-ProcessTree([int]$Id) {
  $children = Get-CimInstance Win32_Process | Where-Object { $_.ParentProcessId -eq $Id } | Select-Object -ExpandProperty ProcessId
  foreach ($child in $children) { Stop-ProcessTree $child }
  Stop-Process -Id $Id -Force -ErrorAction SilentlyContinue
}
try { Wait-Process -Id $backend.Id } finally { Stop-ProcessTree $frontend.Id; Stop-ProcessTree $backend.Id }
