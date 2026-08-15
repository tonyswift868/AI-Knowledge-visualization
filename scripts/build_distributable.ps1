$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Release = Join-Path $Root 'release\distributable'
$Work = Join-Path $Root 'tmp\pyinstaller-work'
$Spec = Join-Path $Root 'MentorFlow.spec'
$VersionFile = Join-Path $Root 'mentorflow-version.json'
$Verifier = Join-Path $PSScriptRoot 'verify_windows_package.py'

if (-not (Test-Path $Python)) { throw 'Run scripts\bootstrap.ps1 first.' }
if (-not (Test-Path $VersionFile)) { throw "Single-source version manifest is missing: $VersionFile" }
if (-not (Test-Path $Verifier)) { throw "Package verifier is missing: $Verifier" }
if (-not (Test-Path (Join-Path $Root 'release\native\MentorFlow.ico'))) {
    & $Python (Join-Path $PSScriptRoot 'build_desktop_icon.py')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

New-Item -ItemType Directory -Path $Release -Force | Out-Null
New-Item -ItemType Directory -Path $Work -Force | Out-Null
Push-Location $Root
try {
    & $Python -m PyInstaller --noconfirm --distpath $Release --workpath $Work $Spec
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}

$App = Join-Path $Release 'MentorFlow\MentorFlow.exe'
$GeneratedSelfCheckExe = Join-Path $Release 'MentorFlow\MentorFlowSelfCheck.exe'
if (-not (Test-Path $App)) { throw 'Distributable executable was not produced.' }
if (-not (Test-Path $GeneratedSelfCheckExe)) { throw 'Standalone self-check executable was not produced.' }
Copy-Item -LiteralPath (Join-Path $Root 'docs\distribution-readme.txt') `
    -Destination (Join-Path $Release 'MentorFlow\使用说明.txt') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'start_ui_review.ps1') `
    -Destination (Join-Path $Release 'MentorFlow\启动UI评审.ps1') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'start_ui_review.cmd') `
    -Destination (Join-Path $Release 'MentorFlow\启动UI评审.cmd') -Force
Copy-Item -LiteralPath $VersionFile -Destination (Join-Path $Release 'MentorFlow\mentorflow-version.json') -Force

# Keep the frozen payload and user data separate.  SELF-CHECK contains the
# auditable source helper and cmd.exe entrypoints.  The standalone console EXE
# is copied into SELF-CHECK so the whole self-check surface is self-contained.
$SelfCheck = Join-Path $Release 'MentorFlow\SELF-CHECK'
New-Item -ItemType Directory -Path $SelfCheck -Force | Out-Null
Copy-Item -LiteralPath $GeneratedSelfCheckExe -Destination (Join-Path $SelfCheck 'MentorFlowSelfCheck.exe') -Force
Remove-Item -LiteralPath $GeneratedSelfCheckExe -Force
Copy-Item -LiteralPath $Verifier -Destination (Join-Path $SelfCheck 'verify_windows_package.py') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'mentorflow-check.bat') -Destination (Join-Path $SelfCheck 'mentorflow-check.bat') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'mentorflow-update.bat') -Destination (Join-Path $SelfCheck 'mentorflow-update.bat') -Force

& $Python $Verifier build-manifest --package-root (Join-Path $Release 'MentorFlow')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python $Verifier verify --package-root (Join-Path $Release 'MentorFlow') --no-data-check --no-smoke
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$PackageVersion = (Get-Content -LiteralPath $VersionFile -Raw | ConvertFrom-Json).version
Write-Host "Distributable application: $App (version $PackageVersion)" -ForegroundColor Green
Write-Host "Package self-check: $(Join-Path $SelfCheck 'mentorflow-check.bat')" -ForegroundColor Green
