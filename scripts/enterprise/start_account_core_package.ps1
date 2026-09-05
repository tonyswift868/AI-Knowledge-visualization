[CmdletBinding()]
param([switch]$ValidateOnly)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# Do not inherit a development PowerShell module path or load user modules.
$env:PSModulePath = Join-Path $PSHOME 'Modules'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -Force

# Release-time trust anchors, substituted by prepare_account_core_package.py.
# Runtime JSON cannot select identity providers, TLS policy, tenants or paths.
$expectedManifest = '__MANIFEST_SHA256__'
$expectedVerifier = '__VERIFIER_SHA256__'
$expectedCertificate = 'EF9EA28E93E89C83B2379FCA65CFAC2D945CCE856EC814D63CBA018B0362C005'
$release = Split-Path -Parent $MyInvocation.MyCommand.Path
$program = Join-Path $release 'MentorFlow'
$config = Join-Path $release 'Runtime\launch.json'
$manifest = Join-Path $program 'SELF-CHECK\package-manifest.json'
$verifier = Join-Path $program 'SELF-CHECK\MentorFlowSelfCheck.exe'
$certificate = Join-Path $release 'Config\edge-tls-ca.crt'

function Assert-NoAlias([string]$Path, [bool]$MustExist = $true) {
    $cursor = [IO.Path]::GetFullPath($Path)
    if ($MustExist -and -not (Test-Path -LiteralPath $cursor)) { throw 'Required release path is missing.' }
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Aliased release/runtime paths are forbidden.' }
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
}

function Read-RuntimeProfile([string]$Path) {
    Assert-NoAlias $Path
    if ((Get-Item -LiteralPath $Path).Length -gt 1024) { throw 'Runtime configuration exceeds size limit.' }
    $raw = [IO.File]::ReadAllText($Path, (New-Object Text.UTF8Encoding($false, $true)))
    # A deliberately narrow JSON grammar: two unique string properties only.
    # Reject unknown/duplicate/case-variant keys, escapes, nested values and secrets.
    $pair = '"(?:schema|data_profile)"\s*:\s*"[A-Za-z0-9._-]+"'
    if ($raw -cnotmatch ('\A\s*\{\s*' + $pair + '\s*,\s*' + $pair + '\s*\}\s*\z')) {
        throw 'Invalid runtime configuration structure or keys.'
    }
    $keys = [regex]::Matches($raw, '"(schema|data_profile)"\s*:')
    if ($keys.Count -ne 2 -or $keys[0].Groups[1].Value -ceq $keys[1].Groups[1].Value) {
        throw 'Duplicate or missing runtime configuration key.'
    }
    $value = $raw | ConvertFrom-Json
    if ($value.schema -cne 'mentorflow.desktop-runtime.v1' -or $value.data_profile -cnotmatch '\A[a-z][a-z0-9-]{0,39}\z') {
        throw 'Invalid runtime configuration value.'
    }
    return $value.data_profile
}

function New-CleanProcess([string]$Executable) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $Executable
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.WorkingDirectory = $program
    $info.EnvironmentVariables.Clear()
    foreach ($key in @('SystemRoot','WINDIR','SystemDrive','COMSPEC','USERPROFILE','LOCALAPPDATA','APPDATA','USERNAME','USERDOMAIN','PROGRAMDATA','PROGRAMFILES','PROGRAMFILES(X86)')) {
        $value = [Environment]::GetEnvironmentVariable($key)
        if ($null -ne $value) { $info.EnvironmentVariables[$key] = $value }
    }
    $info.EnvironmentVariables['PATH'] = "$env:SystemRoot\System32;$env:SystemRoot"
    $info.EnvironmentVariables['TEMP'] = $runtimeTemp
    $info.EnvironmentVariables['TMP'] = $runtimeTemp
    return $info
}

try {
    $runtimeProfile = Read-RuntimeProfile $config
    foreach ($path in @($program,$manifest,$verifier,$certificate)) { Assert-NoAlias $path }
    foreach ($binding in @(@($manifest,$expectedManifest),@($verifier,$expectedVerifier),@($certificate,$expectedCertificate))) {
        if ((Get-FileHash -LiteralPath $binding[0] -Algorithm SHA256).Hash -cne $binding[1]) {
            throw 'Release trust anchor integrity check failed.'
        }
    }
    $vault = Join-Path 'E:\Codex\Data\MentorFlow-E107-PackageUAT' $runtimeProfile
    $runtimeTemp = Join-Path $vault 'runtime-temp'
    Assert-NoAlias $runtimeTemp $false
    New-Item -ItemType Directory -Path $runtimeTemp -Force | Out-Null
    Assert-NoAlias $runtimeTemp

    # Verify every immutable payload file with the unchanged, shipped helper.
    # Never run its optional database inspection or direct-EXE smoke path.
    $checkInfo = New-CleanProcess $verifier
    $checkInfo.Arguments = 'verify --package-root "' + $program + '" --no-data-check --no-smoke'
    $checkInfo.RedirectStandardOutput = $true
    $checkInfo.RedirectStandardError = $true
    $check = [Diagnostics.Process]::Start($checkInfo)
    $output = $check.StandardOutput.ReadToEndAsync()
    $errors = $check.StandardError.ReadToEndAsync()
    $check.WaitForExit()
    if ($check.ExitCode -ne 0 -or $output.Result -notmatch 'MENTORFLOW_PACKAGE_CHECK=PASS') {
        throw ('Package integrity check failed. ' + $errors.Result)
    }
    Write-Output 'LEGAL_CONFIG_ACCEPTED=true'
    Write-Output 'MENTORFLOW_PACKAGE_CHECK=PASS'
    if ($ValidateOnly) { exit 0 }

    $info = New-CleanProcess (Join-Path $program 'MentorFlow.exe')
    $values = @{
        MENTORFLOW_ACCOUNT_VAULT_ROOT = $vault
        MENTORFLOW_DATA_DIR = $vault
        MENTORFLOW_USER_DATA_ROOT = (Join-Path $vault 'prelaunch')
        MENTORFLOW_DB = (Join-Path $vault 'prelaunch\data\app.db')
        MENTORFLOW_ORM_DB = (Join-Path $vault 'prelaunch\data\mentorflow_v2.db')
        MENTORFLOW_DESKTOP_FORCE_LOGIN_GATE = '1'
        MENTORFLOW_DISABLE_PROVIDER_AUTORESTORE = '1'
        MENTORFLOW_DESKTOP_OIDC_ISSUER = 'https://localhost:18080/realms/mentorflow'
        MENTORFLOW_DESKTOP_OIDC_CLIENT_ID = 'mentorflow-desktop'
        MENTORFLOW_DESKTOP_OIDC_AUDIENCE = 'mentorflow-api'
        MENTORFLOW_ENTERPRISE_API_BASE_URL = 'https://localhost:18080'
        MENTORFLOW_DESKTOP_OIDC_CALLBACK_PORT = '0'
        SSL_CERT_FILE = $certificate
        REQUESTS_CA_BUNDLE = $certificate
        # Existing preview TLS pin only; no user-supplied Chromium flags.
        QTWEBENGINE_CHROMIUM_FLAGS = '--ignore-certificate-errors-spki-list=abqvIMwFmsvQQW2qdQ47FYQb7JJZcu4Iy7Qr5hoIcRs='
    }
    foreach ($key in $values.Keys) { $info.EnvironmentVariables[$key] = $values[$key] }
    # No organization/workspace hints: use the validated server /auth/me context.
    $appProcess = [Diagnostics.Process]::Start($info)
    Write-Output ('MENTORFLOW_PID=' + $appProcess.Id)
    $appProcess.WaitForExit()
    $appProcess.Refresh()
    Write-Output ('MENTORFLOW_EXIT=' + $appProcess.ExitCode)
    exit $appProcess.ExitCode
} catch {
    [Console]::Error.WriteLine('MENTORFLOW_LAUNCH_REJECTED: ' + $_.Exception.Message)
    exit 2
}
