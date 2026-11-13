#!/usr/bin/env pwsh
[CmdletBinding()]
param(
    [string]$TargetScript = (Join-Path $PSScriptRoot 'open-workbench.ps1'),
    [string]$ShortcutName = 'MentorFlow 开发工作台.lnk'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if (-not (Test-Path -LiteralPath $TargetScript -PathType Leaf)) {
    throw "启动脚本不存在：$TargetScript"
}

$desktop = [Environment]::GetFolderPath('Desktop')
if ([string]::IsNullOrWhiteSpace($desktop)) {
    throw '无法找到当前用户的 Windows 桌面目录。'
}

$shortcutPath = Join-Path $desktop $ShortcutName
$pwsh = (Get-Command pwsh.exe -ErrorAction SilentlyContinue).Source
if ([string]::IsNullOrWhiteSpace($pwsh)) { $pwsh = (Get-Command powershell.exe -ErrorAction Stop).Source }

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $pwsh
$shortcut.Arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "' + $TargetScript + '"'
$shortcut.WorkingDirectory = Split-Path -Parent $TargetScript
$shortcut.WindowStyle = 1
$shortcut.Description = 'MentorFlow 开发工作台（SSH 隧道后打开 Kuma、Beszel 和账号页）'
$shortcut.IconLocation = "$env:SystemRoot\System32\SHELL32.dll,14"
$shortcut.Save()

Write-Output "已创建桌面快捷方式：$shortcutPath"
