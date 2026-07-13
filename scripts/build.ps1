$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
& npm.cmd --prefix (Join-Path $Root 'frontend') run build
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (-not (Test-Path (Join-Path $Root 'frontend\dist\index.html'))) { throw 'frontend/dist/index.html missing' }
if (-not (Get-ChildItem (Join-Path $Root 'frontend\dist\assets') -File -ErrorAction SilentlyContinue)) { throw 'frontend/dist/assets missing' }
Write-Host 'Frontend dist ready; FastAPI serves it in production mode.' -ForegroundColor Green
