[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Version,
    [Parameter(Mandatory)][ValidateSet('stable','beta')][string]$Channel,
    [Parameter(Mandatory)][string]$ReleaseNotes,
    [Parameter(Mandatory)][string]$Origin,
    [Parameter(Mandatory)][string]$SourceSha,
    [Parameter(Mandatory)][string]$TreeSha,
    [string]$TargetDirectory = 'release/distributable/MentorFlow',
    [string]$OutputDirectory = 'release/feed',
    [string]$BaseDirectory,
    [string]$PythonPath = 'python',
    [string]$SigningKey = $env:MENTORFLOW_RELEASE_SIGNING_KEY,
    [string]$KeyId = $env:MENTORFLOW_RELEASE_KEY_ID,
    [string]$KeyPasswordEnv,
    [switch]$DevelopmentSigning,
    [string]$MinSupportedVersion = '1.0.0',
    [string]$MinUpdaterVersion = '1.0.0',
    [switch]$Mandatory,
    [ValidateSet('','security','protocol','data_compatibility')][string]$MandatoryReason = '',
    [Nullable[long]]$MetadataVersion,
    [ValidateRange(1,31)][int]$ExpiryDays = 7,
    [ValidateRange(0,100)][int]$RolloutPercentage = 100,
    [string]$RolloutSeed = 'mentorflow-release-v1',
    [string]$CertificateThumbprint,
    [string]$TimestampServer
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# Resolve caller-relative inputs before importing the repository's modules.
$releaseArgs = @('-B', '-m', 'scripts.build_release', '--version', $Version, '--channel', $Channel,
    '--notes', $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ReleaseNotes),
    '--origin', $Origin, '--source-sha', $SourceSha, '--tree-sha', $TreeSha,
    '--target', $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($TargetDirectory),
    '--output', $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputDirectory),
    '--min-supported-version', $MinSupportedVersion, '--min-updater-version', $MinUpdaterVersion,
    '--expiry-days', "$ExpiryDays", '--rollout-percentage', "$RolloutPercentage", '--rollout-seed', $RolloutSeed)
if ($BaseDirectory) { $releaseArgs += @('--base', $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($BaseDirectory)) }
if ($SigningKey) { $releaseArgs += @('--signing-key', $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($SigningKey)) }
if ($KeyId) { $releaseArgs += @('--key-id', $KeyId) }
if ($KeyPasswordEnv) { $releaseArgs += @('--key-password-env', $KeyPasswordEnv) }
if ($DevelopmentSigning) { $releaseArgs += '--development-signing' }
if ($Mandatory) { $releaseArgs += '--mandatory' }
if ($MandatoryReason) { $releaseArgs += @('--mandatory-reason', $MandatoryReason) }
if ($null -ne $MetadataVersion) { $releaseArgs += @('--metadata-version', "$MetadataVersion") }
if ($CertificateThumbprint) { $releaseArgs += @('--certificate-thumbprint', $CertificateThumbprint) }
if ($TimestampServer) { $releaseArgs += @('--timestamp-server', $TimestampServer) }
$releasePython = (Get-Command $PythonPath -ErrorAction Stop).Source
Push-Location (Split-Path -Parent $PSScriptRoot)
try {
    & $releasePython @releaseArgs
    if ($LASTEXITCODE -ne 0) { throw "Release builder failed with exit code $LASTEXITCODE" }
} finally { Pop-Location }
