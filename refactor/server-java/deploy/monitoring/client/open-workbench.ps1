#!/usr/bin/env pwsh
[CmdletBinding()]
param(
    [int]$KumaLocalPort = 13001,
    [int]$KumaRemotePort = 3001,
    [int]$BeszelLocalPort = 18090,
    [int]$BeszelRemotePort = 8090,
    [int]$AccountsLocalPort = 18091,
    [int]$AccountsRemotePort = 18081,
    [string]$SshHost = '89.45.45.28',
    [string]$SshUser = 'mentorflow-admin',
    [string]$IdentityFile = 'C:\Users\21533\.ssh\mentorflow_server',
    [string]$SshExecutable = 'ssh.exe',
    [int]$WaitSeconds = 20,
    [switch]$NoUi,
    [switch]$SkipBrowser
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-TcpPort {
    param([string]$HostName, [int]$Port)
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $async = $client.BeginConnect($HostName, $Port, $null, $null)
        if (-not $async.AsyncWaitHandle.WaitOne(500)) { return $false }
        $client.EndConnect($async)
        return $true
    } catch { return $false }
    finally { $client.Dispose() }
}

function Test-HttpReady {
    param([string]$Url, [string]$ExpectedMarker)
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5 -MaximumRedirection 3
        if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 400) { return $false }
        return ([string]$response.Content -match [regex]::Escape($ExpectedMarker))
    } catch {
        return $false
    }
}

function Show-WorkbenchError {
    param([string]$Message)
    if ($NoUi) { [Console]::Error.WriteLine([string]$Message); return }
    try {
        Add-Type -AssemblyName System.Windows.Forms
        [void][System.Windows.Forms.MessageBox]::Show($Message, 'MentorFlow 开发工作台', 'OK', 'Error')
    } catch { Write-Error $Message }
}

function Open-WorkbenchPages {
    param([string]$KumaUrl, [string]$BeszelUrl, [string]$AccountsUrl)
    if ($SkipBrowser) { return }
    Start-Process $KumaUrl
    Start-Process $BeszelUrl
    Start-Process $AccountsUrl
}

$kumaUrl = "http://127.0.0.1:$KumaLocalPort/"
$beszelUrl = "http://127.0.0.1:$BeszelLocalPort/"
$accountsUrl = "http://127.0.0.1:$AccountsLocalPort/admin/accounts"
$sshProcess = $null
$stdoutPath = Join-Path $env:TEMP "mentorflow-workbench-$PID.out.log"
$stderrPath = Join-Path $env:TEMP "mentorflow-workbench-$PID.err.log"

try {
    if (-not (Test-Path -LiteralPath $IdentityFile -PathType Leaf)) {
        throw "SSH 密钥文件不存在：$IdentityFile。请确认电脑上已有 mentorflow_server 密钥。"
    }

    $kumaTcp = Test-TcpPort -HostName '127.0.0.1' -Port $KumaLocalPort
    $beszelTcp = Test-TcpPort -HostName '127.0.0.1' -Port $BeszelLocalPort
    $accountsTcp = Test-TcpPort -HostName '127.0.0.1' -Port $AccountsLocalPort
    $kumaReady = $kumaTcp -and (Test-HttpReady -Url $kumaUrl -ExpectedMarker 'Uptime Kuma')
    $beszelReady = $beszelTcp -and (Test-HttpReady -Url $beszelUrl -ExpectedMarker 'Beszel')
    $accountsReady = $accountsTcp -and (Test-HttpReady -Url $accountsUrl -ExpectedMarker 'MentorFlow 账号')

    if ($kumaReady -and $beszelReady -and $accountsReady) {
        Open-WorkbenchPages -KumaUrl $kumaUrl -BeszelUrl $beszelUrl -AccountsUrl $accountsUrl
        exit 0
    }

    if (($kumaTcp -and -not $kumaReady) -or ($beszelTcp -and -not $beszelReady) -or ($accountsTcp -and -not $accountsReady)) {
        $occupied = @()
        if ($kumaTcp -and -not $kumaReady) { $occupied += "Kuma 本机端口 $KumaLocalPort" }
        if ($beszelTcp -and -not $beszelReady) { $occupied += "Beszel 本机端口 $BeszelLocalPort" }
        if ($accountsTcp -and -not $accountsReady) { $occupied += "账号本机端口 $AccountsLocalPort" }
        throw "端口已被其他程序占用：$($occupied -join '、')。请关闭占用程序后重试。"
    }

    $sshArgs = @(
        '-i', $IdentityFile,
        '-o', 'IdentitiesOnly=yes',
        '-o', 'BatchMode=yes',
        '-N',
        '-o', 'ExitOnForwardFailure=yes',
        '-o', 'ServerAliveInterval=30',
        '-o', 'ServerAliveCountMax=3'
    )
    if (-not $kumaReady) { $sshArgs += @('-L', "127.0.0.1:$KumaLocalPort`:127.0.0.1:$KumaRemotePort") }
    if (-not $beszelReady) { $sshArgs += @('-L', "127.0.0.1:$BeszelLocalPort`:127.0.0.1:$BeszelRemotePort") }
    if (-not $accountsReady) { $sshArgs += @('-L', "127.0.0.1:$AccountsLocalPort`:127.0.0.1:$AccountsRemotePort") }
    $sshArgs += "$SshUser@$SshHost"
    Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
    $sshProcess = Start-Process -FilePath $SshExecutable -ArgumentList $sshArgs -WindowStyle Hidden -PassThru `
        -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath

    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    do {
        Start-Sleep -Milliseconds 500
        $kumaReady = Test-HttpReady -Url $kumaUrl -ExpectedMarker 'Uptime Kuma'
        $beszelReady = Test-HttpReady -Url $beszelUrl -ExpectedMarker 'Beszel'
        $accountsReady = Test-HttpReady -Url $accountsUrl -ExpectedMarker 'MentorFlow 账号'
        if ($kumaReady -and $beszelReady -and $accountsReady) {
            Open-WorkbenchPages -KumaUrl $kumaUrl -BeszelUrl $beszelUrl -AccountsUrl $accountsUrl
            exit 0
        }
        if ($sshProcess.HasExited) { break }
    } while ((Get-Date) -lt $deadline)

    $sshError = ''
    if (Test-Path -LiteralPath $stderrPath) {
        $sshError = Get-Content -LiteralPath $stderrPath -Raw -ErrorAction SilentlyContinue
        if ($null -eq $sshError) { $sshError = '' } else { $sshError = $sshError.ToString().Trim() }
    }
    if ($sshProcess.HasExited -and $sshError) {
        throw "无法连接服务器或建立 SSH 隧道：$sshError"
    }
    throw "SSH 隧道未在 $WaitSeconds 秒内连通。请检查服务器网络、密钥权限和本机端口 $KumaLocalPort/$BeszelLocalPort/$AccountsLocalPort。"
} catch {
    if ($sshProcess -and -not $sshProcess.HasExited) {
        Stop-Process -Id $sshProcess.Id -Force -ErrorAction SilentlyContinue
    }
    $errorMessage = if ($null -ne $_ -and $null -ne $_.Exception) { $_.Exception.Message } else { '工作台隧道启动失败。' }
    if ([string]::IsNullOrWhiteSpace([string]$errorMessage)) { $errorMessage = '工作台隧道启动失败。' }
    Show-WorkbenchError -Message $errorMessage
    exit 1
} finally {
    Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
}
