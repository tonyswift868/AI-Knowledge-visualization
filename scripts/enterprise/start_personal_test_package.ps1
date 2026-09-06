[CmdletBinding()]
param([switch]$ValidateOnly)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
# Do not inherit a development PowerShell module path or load user modules.
$env:PSModulePath = Join-Path $PSHOME 'Modules'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -Force

# Frozen 20260906 personal-test release anchors; not a general distribution template.
# Runtime JSON cannot select identity providers, TLS policy, tenants or paths.
$expectedManifest = 'A786BD894ABEA739B3B3F1A8A797B160CC8B56E88CF4F40CE580D8AD78BA9380'
$expectedVerifier = 'BDB4FA23422BA25159F63D53757A718031E4AC3C61D9B2A89E7B9F3127197A67'
$expectedCertificate = 'EF9EA28E93E89C83B2379FCA65CFAC2D945CCE856EC814D63CBA018B0362C005'
$release = Split-Path -Parent $MyInvocation.MyCommand.Path
$program = Join-Path $release 'MentorFlow'
$config = Join-Path $release 'Runtime\launch.json'
$manifest = Join-Path $program 'SELF-CHECK\package-manifest.json'
$verifier = Join-Path $program 'SELF-CHECK\MentorFlowSelfCheck.exe'
$certificate = Join-Path $release 'Config\edge-tls-ca.crt'
$launchClock = [Diagnostics.Stopwatch]::StartNew()
$launchTiming = [ordered]@{ schema='mentorflow.launch-timing.v1'; started_utc=[DateTime]::UtcNow.ToString('o'); launcher_pid=$PID }
function Record-LaunchPhase([string]$Name) {
    $launchTiming[$Name] = [Math]::Round($launchClock.Elapsed.TotalMilliseconds, 2)
    [IO.File]::WriteAllText((Join-Path $runtimeTemp ('launch-timing-' + $PID + '.json')), ($launchTiming | ConvertTo-Json), [Text.UTF8Encoding]::new($false))
}

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

# Existing preview transport only; no deployment, dynamic destinations or credentials.
$script:ownedTunnel = $null
$progressWindow = $null
function Test-SupportedEndpoint {
    $curl = Join-Path $env:SystemRoot 'System32\curl.exe'
    $document = & $curl --silent --fail --noproxy '*' --connect-timeout 2 --max-time 4 --cacert $certificate 'https://localhost:18080/realms/mentorflow/.well-known/openid-configuration' 2>$null
    if ($LASTEXITCODE -ne 0) { return $false }
    try {
        $discovery = ($document -join "`n") | ConvertFrom-Json
        return ($discovery.issuer -ceq 'https://localhost:18080/realms/mentorflow')
    } catch { return $false }
}
function Ensure-SupportedTunnel {
    if (Test-SupportedEndpoint) { Write-Output 'TUNNEL_TLS=EXISTING_PINNED_ENDPOINT'; return }
    $ssh = Join-Path $env:SystemRoot 'System32\OpenSSH\ssh.exe'
    $key = 'E:\Codex\Secrets\MentorFlow\ssh\mentorflow-e1-ops-ed25519'
    if (-not (Test-Path -LiteralPath $key)) {
        Write-Warning '安全连接密钥不可用。登录窗将显示连接提示；请按测试访问说明恢复密钥后重启。'
        return
    }
    Assert-NoAlias $key
    $sshInfo = New-CleanProcess $ssh
    $sshInfo.Arguments = '-N -T -i "' + $key + '" -o BatchMode=yes -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes -o ConnectTimeout=8 -o ServerAliveInterval=15 -o ServerAliveCountMax=2 -L 127.0.0.1:18080:127.0.0.1:443 mentorflow-admin@89.45.45.28'
    $script:ownedTunnel = [Diagnostics.Process]::Start($sshInfo)
    for ($attempt=0; $attempt -lt 6; $attempt++) {
        if (Test-SupportedEndpoint) { Write-Output 'TUNNEL_TLS=OWNED_SSH_PINNED_ENDPOINT'; return }
        if ($script:ownedTunnel.HasExited) { break }
        Start-Sleep -Milliseconds 500
    }
    Write-Warning '安全连接暂时不可用。请在登录窗查看提示；网络恢复后重试。'
}
function Close-OwnedTunnel {
    if ($null -ne $script:ownedTunnel) {
        if (-not $script:ownedTunnel.HasExited) { $script:ownedTunnel.Kill(); $script:ownedTunnel.WaitForExit(5000) | Out-Null }
        $script:ownedTunnel.Dispose()
        $script:ownedTunnel = $null
    }
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

    # Source template for the next integrated release: rebind the frozen helper
    # and manifest anchors when building that release. The preserved old launcher
    # is not modified. launch-gate never opens a database or runs application code.
    $progressWindow = $null
    if (-not $ValidateOnly) {
        Add-Type -AssemblyName System.Windows.Forms
        $progressWindow = New-Object System.Windows.Forms.Form
        $progressWindow.Text = 'MentorFlow — 安全检查'
        $progressWindow.Width = 440
        $progressWindow.Height = 125
        $progressWindow.StartPosition = 'CenterScreen'
        $progressWindow.ControlBox = $false
        $progressLabel = New-Object System.Windows.Forms.Label
        $progressLabel.Dock = 'Fill'
        $progressLabel.TextAlign = 'MiddleCenter'
        $progressLabel.Text = '正在验证程序完整性并准备安全连接。首次验证可能需要较长时间。'
        $progressWindow.Controls.Add($progressLabel)
        $progressWindow.Show()
        [System.Windows.Forms.Application]::DoEvents()
        Record-LaunchPhase 'security_feedback_visible_ms'
    }
    $checkInfo = New-CleanProcess $verifier
    # Four bounded readers beat serial in the 20260906 isolated-copy benchmark.
    # The general helper still defaults to one reader on unmeasured machines.
    $checkInfo.Arguments = 'launch-gate --package-root "' + $program + '" --state-path "' + (Join-Path $runtimeTemp 'verification-state.dpapi') + '" --hash-workers 4 --expected-manifest-sha256 ' + $expectedManifest
    $checkInfo.RedirectStandardOutput = $true
    $checkInfo.RedirectStandardError = $true
    Record-LaunchPhase 'integrity_started_ms'
    $check = [Diagnostics.Process]::Start($checkInfo)
    $output = $check.StandardOutput.ReadToEndAsync()
    $errors = $check.StandardError.ReadToEndAsync()
    # Independent trusted OS connection setup overlaps full payload verification.
    # The application still cannot start until the complete verifier passes.
    if (-not $ValidateOnly) {
        Record-LaunchPhase 'connection_started_ms'
        Ensure-SupportedTunnel
        Record-LaunchPhase 'connection_ready_ms'
    }
    while (-not $check.WaitForExit(100)) {
        if ($null -ne $progressWindow) { [System.Windows.Forms.Application]::DoEvents() }
    }
    if ($check.ExitCode -ne 0 -or $output.Result -notmatch 'MENTORFLOW_PACKAGE_CHECK=PASS') {
        throw ('Package integrity check failed. ' + $errors.Result)
    }
    Write-Output 'LEGAL_CONFIG_ACCEPTED=true'
    Record-LaunchPhase 'integrity_pass_ms'
    if ($null -ne $progressWindow) { $progressWindow.Close(); $progressWindow.Dispose(); $progressWindow = $null }
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
    Record-LaunchPhase 'app_process_started_ms'
    Write-Output ('MENTORFLOW_PID=' + $appProcess.Id)
    $appProcess.WaitForExit()
    $appProcess.Refresh()
    $launchTiming['app_exit_code'] = $appProcess.ExitCode
    Record-LaunchPhase 'app_exited_ms'
    Write-Output ('MENTORFLOW_EXIT=' + $appProcess.ExitCode)
    $appExitCode = $appProcess.ExitCode
    Close-OwnedTunnel
    exit $appExitCode
} catch {
    if ($null -ne $progressWindow) { $progressWindow.Close(); $progressWindow.Dispose() }
    Close-OwnedTunnel
    [Console]::Error.WriteLine('MENTORFLOW_LAUNCH_REJECTED: ' + $_.Exception.Message)
    exit 2
}
