param(
  [Parameter(Mandatory=$true)][string]$Zip,
  [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
  [switch]$Install,
  [string]$PreviewId,
  [switch]$Confirm,
  [switch]$Activate
)

$python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
$arguments = @((Join-Path $ProjectRoot 'scripts\install_expedition_art_pack.py'), '--zip', $Zip, '--project-root', $ProjectRoot)
if ($Install) { $arguments += '--install' }
if ($PreviewId) { $arguments += @('--preview-id', $PreviewId) }
if ($Confirm) { $arguments += '--confirm' }
if ($Activate) { $arguments += '--activate' }
& $python @arguments
exit $LASTEXITCODE
