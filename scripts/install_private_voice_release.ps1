[CmdletBinding()]
param(
    [ValidateSet('plan', 'install', 'verify', 'activate', 'rollback')]
    [string]$Action = 'install',

    [string]$PackageRoot = '',
    [string]$RegistrationId = '',
    [string]$ConfigRoot = '',
    [string]$Python = '',
    [switch]$NoActivate
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ([string]::IsNullOrWhiteSpace($Python)) {
    $projectRoot = Split-Path -Parent $PSScriptRoot
    $bundledPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
    $Python = if (Test-Path -LiteralPath $bundledPython -PathType Leaf) {
        $bundledPython
    } else {
        (Get-Command python.exe -ErrorAction Stop).Source
    }
}

$installer = Join-Path $PSScriptRoot 'private_voice_local_installer.py'
$arguments = @($installer, $Action)
if ([string]::IsNullOrWhiteSpace($PackageRoot) -and $Action -in @('plan', 'install')) {
    if (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'PRIVATE_RELEASE_RECEIPT.json')) {
        $PackageRoot = $PSScriptRoot
    } elseif (Test-Path -LiteralPath (Join-Path (Split-Path -Parent $PSScriptRoot) 'PRIVATE_RELEASE_RECEIPT.json')) {
        $PackageRoot = Split-Path -Parent $PSScriptRoot
    }
}
if (-not [string]::IsNullOrWhiteSpace($PackageRoot)) {
    $arguments += @('--package-root', $PackageRoot)
}
if (-not [string]::IsNullOrWhiteSpace($RegistrationId)) {
    $arguments += @('--registration-id', $RegistrationId)
}
if (-not [string]::IsNullOrWhiteSpace($ConfigRoot)) {
    $arguments += @('--config-root', $ConfigRoot)
}
if ($NoActivate) {
    $arguments += '--no-activate'
}

& $Python @arguments
exit $LASTEXITCODE
