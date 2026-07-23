param(
    [string]$EvidenceDirectory = "",
    [switch]$IncludeLegacyBrowser
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$frontend = Join-Path $projectRoot "frontend"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "Project Python was not found at $python"
}

if (-not $EvidenceDirectory) {
    $EvidenceDirectory = Join-Path $projectRoot "artifacts\fasttrack2_w6_e2e"
}
$EvidenceDirectory = [System.IO.Path]::GetFullPath($EvidenceDirectory)
New-Item -ItemType Directory -Force -Path $EvidenceDirectory | Out-Null

function Invoke-Checked {
    param(
        [string]$Label,
        [scriptblock]$Command
    )
    $log = Join-Path $EvidenceDirectory "$Label.txt"
    & $Command 2>&1 | Tee-Object -FilePath $log
    if ($LASTEXITCODE -ne 0) {
        throw "$Label failed with exit code $LASTEXITCODE. See $log"
    }
}

Push-Location $projectRoot
try {
    Invoke-Checked "backend_w6_http_matrix" {
        & $python -m pytest `
            backend/tests/test_w6_acceptance_matrix.py `
            backend/tests/test_v5_provider_mode.py::test_provider_failure_never_falls_back_to_fake `
            backend/tests/test_learning_run.py::test_duplicate_terminal_is_idempotent_by_key_and_rejects_new_submission `
            backend/tests/test_learning_run.py::test_expedition_failure_records_pending_and_retry_delivers_once `
            backend/tests/test_v6_archive_import.py::test_zip_slip_is_rejected `
            backend/tests/test_v6_archive_import.py::test_cancel_preview_cleans_temp_directory `
            backend/tests/test_kks_runtime.py `
            -q
    }

    if ($IncludeLegacyBrowser) {
        Push-Location $frontend
        try {
            Invoke-Checked "browser_w6_matrix" {
                & npm.cmd exec -- playwright test e2e/demo.spec.ts --grep `
                    "home exposes|real provider failure|normal ZIP preview|malicious ZIP traversal"
            }
        }
        finally {
            Pop-Location
        }
    }

    git status --short | Set-Content -Encoding UTF8 (
        Join-Path $EvidenceDirectory "git_status_after.txt"
    )
    git rev-parse HEAD | Set-Content -Encoding ASCII (
        Join-Path $EvidenceDirectory "head.txt"
    )
}
finally {
    Pop-Location
}

Write-Output "W6 FAST-TRACK 2 HTTP acceptance passed. Evidence: $EvidenceDirectory"
if (-not $IncludeLegacyBrowser) {
    Write-Output "Legacy browser checks were not requested; pass -IncludeLegacyBrowser after updating stale demo selectors."
}
