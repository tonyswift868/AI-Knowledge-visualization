$ErrorActionPreference = 'Stop'

# This launcher is deliberately tied to the marked E: drive review copy.
# It must never point the auth-free review mode at the canonical data root.
$projectRoot = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $projectRoot 'release\distributable\MentorFlow\MentorFlow.exe'
$dataRoot = 'E:\MentorFlowData\ui-review'
$marker = Join-Path $dataRoot 'ui-review-manifest.json'

if (-not (Test-Path -LiteralPath $exe)) {
    throw "MentorFlow executable is missing: $exe. Run scripts\build_distributable.ps1 first."
}
if (-not (Test-Path -LiteralPath $marker)) {
    throw "The isolated UI review data root is missing or unmarked: $dataRoot"
}

$env:MENTORFLOW_USER_DATA_ROOT = $dataRoot
$env:MENTORFLOW_LOCAL_DATA_MODE = '1'
$env:MENTORFLOW_AUTH_REQUIRED = '0'
$env:MENTORFLOW_UI_REVIEW_MODE = '1'
# The review launcher must exercise the same restart path as the shipped app.
# Smoke tests may opt out, but normal launches always restore the E: preset.
$env:MENTORFLOW_DISABLE_PROVIDER_AUTORESTORE = '0'
$env:MENTORFLOW_DESKTOP_SMOKE_SECONDS = '0'

Start-Process -FilePath $exe | Out-Null
