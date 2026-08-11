$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Backend = Join-Path $Root 'backend'
$Catalog = 'E:\AI_Tools\GPTSoVITS\voice_catalog\teacher_voice_pack_catalog.v1.json'
$PackRoot = 'E:\AI_Tools\GPTSoVITS\voice_packs'
$RuntimeUrl = 'http://127.0.0.1:9881'
$LogRoot = 'E:\AI_Tools\GPTSoVITS\voice_runtime'

foreach ($Path in @($Python, $Catalog)) {
  if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
    throw "Missing required local voice backend input: $Path"
  }
}
if (-not (Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue)) {
  $env:MENTORFLOW_TEACHER_VOICE_PROVIDER = 'pack'
  $env:MENTORFLOW_VOICE_PACK_CATALOG = $Catalog
  $env:MENTORFLOW_VOICE_PACK_ROOT = $PackRoot
  $env:MENTORFLOW_VOICE_PACK_GPT_SOVITS_URL = $RuntimeUrl
  New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null
  $stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
  $outLog = Join-Path $LogRoot "mentorflow_backend_$stamp.out.log"
  $errLog = Join-Path $LogRoot "mentorflow_backend_$stamp.err.log"
  $process = Start-Process -FilePath $Python -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', '8000') -WorkingDirectory $Backend -WindowStyle Hidden -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
  Write-Host "MentorFlow backend PID: $($process.Id)"
  Write-Host "Backend stdout: $outLog"
  Write-Host "Backend stderr: $errLog"
}

for ($index = 0; $index -lt 30; $index++) {
  if (Get-NetTCPConnection -State Listen -LocalPort 8000 -ErrorAction SilentlyContinue) {
    Write-Host 'MentorFlow backend ready on http://127.0.0.1:8000' -ForegroundColor Green
    exit 0
  }
  Start-Sleep -Seconds 1
}
throw 'MentorFlow backend did not listen on port 8000 within 30 seconds'
