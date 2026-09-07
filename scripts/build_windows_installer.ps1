[CmdletBinding()]
param(
    [string]$PayloadRoot = '',
    [string]$OutputRoot = '',
    [string]$NsisPath = 'E:\Codex\App\NSIS-3.12\makensis.exe',
    [string]$PythonPath = '',
    [string]$ProductKey = '',
    [string]$UninstallKey = '',
    [string]$ShortcutFolder = '',
    [string]$ShortcutName = '',
    [string]$UninstallShortcutName = '',
    [string]$DesktopShortcutName = '',
    [string]$DataRootCanonical = '',
    [string]$DataRootLocalAppData = '',
    [string]$InstallDir = '',
    [switch]$ProtectedInstall,
    [string]$BuildScratch = '',
    [string]$SourceSha = '',
    [string]$SourceTree = ''
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$PayloadRoot = if ([string]::IsNullOrWhiteSpace($PayloadRoot)) {
    Join-Path $ProjectRoot 'release\distributable\MentorFlow'
} else { $PayloadRoot }
$OutputRoot = if ([string]::IsNullOrWhiteSpace($OutputRoot)) {
    Join-Path $ProjectRoot 'artifacts\windows-installer'
} else { $OutputRoot }
$VersionFile = Join-Path $ProjectRoot 'mentorflow-version.json'
$VerifierPath = Join-Path $PSScriptRoot 'verify_windows_package.py'
$InstallerScript = Join-Path $ProjectRoot 'installer\MentorFlow.nsi'

function Get-FullPath([string]$Path) {
    return [IO.Path]::GetFullPath($Path)
}

function Assert-ExistingDirectory([string]$Path, [string]$Label) {
    $full = Get-FullPath $Path
    $item = Get-Item -LiteralPath $full -Force -ErrorAction Stop
    if (-not $item.PSIsContainer) { throw "$Label is not a directory: $full" }
    if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label is a symlink/reparse point: $full"
    }
    return $full
}

# This NSIS entry is the legacy per-user installer. A payload containing the
# protected-installation gate must not silently be installed user-writable or
# receive direct-to-product shortcuts. Keep this fail-closed until a qualified
# elevated outer bootstrap replaces this entry; never elevate the old uninstaller.
$payload = Assert-ExistingDirectory $PayloadRoot 'Payload root'
if ((Test-Path -LiteralPath (Join-Path $payload 'MentorFlowInstallGate.exe')) -and -not $ProtectedInstall) {
    throw 'PROTECTED_INSTALLER_REQUIRED: native-gate payload cannot use the legacy per-user NSIS installer. Use the qualified protected-installation bootstrap; do not build or distribute this installer.'
}
if ($ProtectedInstall) {
    if ($SourceSha -notmatch '^[0-9a-f]{40}$' -or $SourceTree -notmatch '^[0-9a-f]{40}$') { throw 'Exact source identities required' }
    if ($InstallDir -or $ProductKey -or $UninstallKey -or $ShortcutFolder -or $ShortcutName -or $UninstallShortcutName -or $DesktopShortcutName -or $DataRootCanonical -or $DataRootLocalAppData) { throw 'Protected installation paths are selected at runtime; build-time metadata overrides are prohibited' }
    $scratchFull = if ($BuildScratch) { [IO.Path]::GetFullPath($BuildScratch).TrimEnd('\') } else { '' }
    $runtimeQualificationScratch = 'D:\CodexScratch\MentorFlowMF-E107-INSTALLER-RUNTIME-QUALIFY-20260907-03'
    if (-not $scratchFull.StartsWith('D:\CodexScratch\MentorFlow\', [StringComparison]::OrdinalIgnoreCase) -and
        -not $scratchFull.Equals($runtimeQualificationScratch, [StringComparison]::OrdinalIgnoreCase)) { throw 'Protected installer requires dedicated D: scratch' }
    $identity = Get-Content -LiteralPath (Join-Path $payload 'SELF-CHECK\source-identity.json') -Raw | ConvertFrom-Json
    if ($identity.source_sha -ne $SourceSha -or $identity.tree_sha -ne $SourceTree) { throw 'Payload source identity mismatch' }
    $manifestPin = (Get-FileHash -LiteralPath (Join-Path $payload 'SELF-CHECK\package-manifest.json') -Algorithm SHA256).Hash.ToLowerInvariant()
}

if (-not (Test-Path -LiteralPath $VersionFile -PathType Leaf)) {
    throw "Single-source version manifest is missing: $VersionFile"
}
if (-not (Test-Path -LiteralPath $VerifierPath -PathType Leaf)) {
    throw "Package verifier is missing: $VerifierPath"
}
if (-not (Test-Path -LiteralPath $InstallerScript -PathType Leaf)) {
    throw "Installer source is missing: $InstallerScript"
}
if (-not (Test-Path -LiteralPath $NsisPath -PathType Leaf)) {
    throw "Verified NSIS 3.12 compiler is missing: $NsisPath"
}

$versionManifest = Get-Content -LiteralPath $VersionFile -Raw | ConvertFrom-Json
if ($versionManifest.schema -ne 'mentorflow.version.v1' -or $versionManifest.product -ne 'MentorFlow') {
    throw "Unsupported MentorFlow version manifest: $VersionFile"
}
$version = [string]$versionManifest.version
if ($version -notmatch '^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$') {
    throw "Invalid MentorFlow version: $version"
}

$payloadExe = Join-Path $payload 'MentorFlow.exe'
if (-not (Test-Path -LiteralPath $payloadExe -PathType Leaf)) {
    throw "Verified payload executable is missing: $payloadExe"
}

# NSIS uses classic Win32 path APIs while materializing and later enumerating
# the installed tree.  Derive a conservative root-path budget from the actual
# verified payload so a custom directory cannot silently omit deep files or
# become impossible to uninstall.  258 leaves room for the terminating NUL
# and for the separator between the install root and the relative path.
$longestPayloadRelativePath = (
    Get-ChildItem -LiteralPath $payload -Recurse -Force |
        ForEach-Object { $_.FullName.Substring($payload.Length).TrimStart('\').Length } |
        Measure-Object -Maximum
).Maximum
# Reserve the runtime .u\<12-hex>\App staging suffix and a legacy DLL margin.
$maxInstallDirChars = 240 - 21 - [int]$longestPayloadRelativePath
if ($maxInstallDirChars -lt 20) {
    throw "Payload paths leave no safe NSIS install-root budget: $maxInstallDirChars characters."
}

$releaseRoot = Get-FullPath (Join-Path $ProjectRoot 'release\distributable')
$outputRootFull = Get-FullPath $OutputRoot
$releasePrefix = $releaseRoot.TrimEnd('\') + '\'
if ($outputRootFull.Equals($releaseRoot, [StringComparison]::OrdinalIgnoreCase) -or
    $outputRootFull.StartsWith($releasePrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw "Installer output must be outside release\distributable: $outputRootFull"
}

if ([string]::IsNullOrWhiteSpace($PythonPath)) {
    $PythonPath = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)) {
    throw "Python verifier runtime is missing: $PythonPath"
}

$nsisVersion = (& $NsisPath /VERSION | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $nsisVersion -notmatch '^v3\.12(?:\.|$)') {
    throw "NSIS compiler is not the verified 3.12 build: $nsisVersion"
}

# Verification is read-only and deliberately skips the data-root and startup
# checks.  It still requires the complete package manifest and all integrity
# checks, so an incomplete/stale/unverified payload cannot be embedded.
$verifyArgs = @(
    $VerifierPath,
    'verify',
    '--package-root', $payload,
    '--no-data-check',
    '--no-smoke'
)
if ($ProtectedInstall) {
    $verifyArgs = @('-B', '-m', 'scripts.prepare_transactional_installer', '--payload', $payload, '--manifest-sha256', $manifestPin)
}
Push-Location $ProjectRoot
try {
    if ($ProtectedInstall) {
        $verificationOutput = & $PythonPath @verifyArgs
        $verifyExit = $LASTEXITCODE
        $verificationOutput | ForEach-Object { Write-Host $_ }
    } else {
        & $PythonPath @verifyArgs
        $verifyExit = $LASTEXITCODE
    }
}
finally { Pop-Location }
if ($verifyExit -ne 0) {
    throw "Payload verification failed with exit code $verifyExit; installer build refused."
}
if ($ProtectedInstall) {
    $verifiedInventory = ($verificationOutput -join "`n") | ConvertFrom-Json
    $spaceKib = [string]$verifiedInventory.estimated_space_kib
    if ($verifiedInventory.status -ne 'PASS' -or $spaceKib -notmatch '^[1-9][0-9]*$' -or
        [decimal]$spaceKib -gt [int]::MaxValue) { throw 'Verified positive installer space estimate required' }
}

New-Item -ItemType Directory -Path $outputRootFull -Force | Out-Null
$outputPath = Join-Path $outputRootFull ("MentorFlow-{0}-win-x64-Setup.exe" -f $version)
if (Test-Path -LiteralPath $outputPath) { throw 'Existing installer output preserved' }
$fileVersion = (($version -split '[+-]', 2)[0] + '.0')
$compileDefines = [System.Collections.Generic.List[string]]::new()
$compileDefines.Add("/DMAX_INSTALL_DIR_CHARS=$maxInstallDirChars")
if ($ProtectedInstall) {
    New-Item -ItemType Directory -Path $BuildScratch -Force | Out-Null
    $helperPath = Join-Path $payload 'MentorFlowInstallGate.exe'
    $compileDefines.Add('/DPROTECTED_INSTALL=1')
    $compileDefines.Add("/DTRANSACTION_HELPER=$helperPath")
    $compileDefines.Add("/DMANIFEST_SHA=$manifestPin")
    $compileDefines.Add("/DSOURCE_SHA=$SourceSha")
    $compileDefines.Add("/DSOURCE_TREE=$SourceTree")
    $compileDefines.Add("/DINSTALL_SPACE_KIB=$spaceKib")
}
if ($ProductKey) { $compileDefines.Add("/DPRODUCT_KEY=$ProductKey") }
if ($UninstallKey) { $compileDefines.Add("/DUNINSTALL_KEY=$UninstallKey") }
if ($ShortcutFolder) { $compileDefines.Add("/DSHORTCUT_FOLDER=$ShortcutFolder") }
if ($ShortcutName) { $compileDefines.Add("/DSHORTCUT_NAME=$ShortcutName") }
if ($UninstallShortcutName) { $compileDefines.Add("/DUNINSTALL_SHORTCUT_NAME=$UninstallShortcutName") }
if ($DesktopShortcutName) { $compileDefines.Add("/DDESKTOP_SHORTCUT_NAME=$DesktopShortcutName") }
if ($DataRootCanonical) { $compileDefines.Add("/DDATA_ROOT_CANONICAL=$DataRootCanonical") }
if ($DataRootLocalAppData) { $compileDefines.Add("/DDATA_ROOT_LOCALAPPDATA=$DataRootLocalAppData") }
if ($InstallDir) { $compileDefines.Add("/DINSTALL_DIR=$InstallDir") }
$makensisArgs = @(
    '/V3',
    "/DPAYLOAD_DIR=$payload",
    "/DPRODUCT_VERSION=$version",
    "/DFILE_VERSION=$fileVersion",
    "/DOUTPUT_FILE=$outputPath"
) + $compileDefines.ToArray() + @($InstallerScript)
& $NsisPath @makensisArgs
if ($LASTEXITCODE -ne 0) {
    throw "NSIS compiler failed with exit code $LASTEXITCODE."
}
if (-not (Test-Path -LiteralPath $outputPath -PathType Leaf)) {
    throw "NSIS did not produce the expected installer: $outputPath"
}

$size = (Get-Item -LiteralPath $outputPath -Force).Length
Write-Host ("MentorFlow installer: {0} ({1:N0} bytes, version {2})" -f $outputPath, $size, $version) -ForegroundColor Green
Write-Host ("Maximum install-directory length: {0} characters" -f $maxInstallDirChars) -ForegroundColor Green
Write-Host 'Authenticode: unsigned (no signing certificate was supplied).' -ForegroundColor Yellow
