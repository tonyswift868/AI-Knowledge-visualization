# Installer-stage source only. Daily startup executes the native onedir gate,
# never this PowerShell script. Invoke after protected full activation succeeds.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$InstallRoot,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{64}$')][string]$ManifestSha256,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$SourceSha,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$TreeSha,
    [string]$ShortcutPath = (Join-Path $env:PUBLIC 'Desktop\MentorFlow.lnk')
)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Shortcut publication is an elevated installation operation.'
}
$root = [IO.Path]::GetFullPath($InstallRoot)
$gate = Join-Path $root 'App\MentorFlowInstallGate.exe'
if (-not (Test-Path -LiteralPath $gate -PathType Leaf)) { throw 'Integrated native gate is missing.' }
$bindings = '--install-root "' + $root + '" --manifest-sha256 ' + $ManifestSha256 + ' --source-sha ' + $SourceSha + ' --tree-sha ' + $TreeSha
$process = Start-Process -FilePath $gate -ArgumentList ('gate ' + $bindings) -WindowStyle Hidden -PassThru -Wait
if ($process.ExitCode -ne 0) { throw 'Protected gate rejected installation; no shortcut was published.' }
$shell = New-Object -ComObject WScript.Shell
$shortcut = $null
try {
    $shortcut = $shell.CreateShortcut([IO.Path]::GetFullPath($ShortcutPath))
    $shortcut.TargetPath = $gate
    $shortcut.Arguments = 'launch ' + $bindings
    $shortcut.WorkingDirectory = Join-Path $root 'App'
    $shortcut.IconLocation = (Join-Path $root 'App\MentorFlow.exe') + ',0'
    $shortcut.Description = 'MentorFlow'
    $shortcut.WindowStyle = 1
    $shortcut.Save()
} finally {
    if ($null -ne $shortcut) { [Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut) | Out-Null }
    [Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell) | Out-Null
}
