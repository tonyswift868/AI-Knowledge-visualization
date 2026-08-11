$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$python = Join-Path $Root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw '请先运行 scripts\bootstrap.ps1' }
# The active teacher voice is the signed local GPT-SoVITS catalog. MiniMax
# remains available only as an explicitly configured legacy compatibility path;
# it is no longer selected by the development launcher.
$env:MENTORFLOW_TEACHER_VOICE_PROVIDER = 'pack'
$env:MENTORFLOW_VOICE_PACK_CATALOG = 'E:\AI_Tools\GPTSoVITS\voice_catalog\teacher_voice_pack_catalog.v1.json'
$env:MENTORFLOW_VOICE_PACK_ROOT = 'E:\AI_Tools\GPTSoVITS\voice_packs'
$env:MENTORFLOW_VOICE_PACK_GPT_SOVITS_URL = 'http://127.0.0.1:9881'
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
