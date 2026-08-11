param(
  [string[]]$GameProcess = @(),
  [switch]$Diagnostic
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
$entry = Join-Path $PSScriptRoot 'desktop_pet.py'
$asset = Join-Path $repoRoot 'artifacts\desktop_pet\qpet-v1\qpet-v1-binary.png'

if (-not (Test-Path -LiteralPath $python)) {
  throw "Repo Python environment not found: $python"
}
if (-not (Test-Path -LiteralPath $asset)) {
  throw "Pet asset not found: $asset"
}

$arguments = @($entry, '--image', $asset)
foreach ($name in $GameProcess) {
  $arguments += @('--game-process', $name)
}
if ($Diagnostic) { $arguments += '--diagnostic' }

& $python @arguments
