[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$PythonPath,
    [string]$OutputRoot = 'E:\Codex\Temp\E107-TrustBoundary'
)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) { throw 'Explicit existing Python runtime required.' }
$env:PYINSTALLER_CONFIG_DIR = 'E:\Codex\Data\cache\pyinstaller'
Push-Location $project
try {
    & $PythonPath -m PyInstaller --noconfirm --onedir --windowed --name MentorFlowInstallGate --paths $project --distpath (Join-Path $OutputRoot 'native') --workpath (Join-Path $OutputRoot 'build') --specpath (Join-Path $OutputRoot 'spec') (Join-Path $PSScriptRoot 'run_windows_install_gate.py')
    if ($LASTEXITCODE -ne 0) { throw 'Native installation gate build failed.' }
} finally {
    Pop-Location
}
