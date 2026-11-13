#!/usr/bin/env powershell
[CmdletBinding()]
param(
    [string]$TargetScript = '',
    [string]$ShortcutName = 'MentorFlow 工作台.lnk',
    [string]$ShortcutDirectory = [Environment]::GetFolderPath('Desktop')
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Windows PowerShell 5.1 leaves $PSScriptRoot empty in param defaults.
if ([string]::IsNullOrWhiteSpace($TargetScript)) { $TargetScript = Join-Path $PSScriptRoot 'open-workbench.ps1' }

if (-not (Test-Path -LiteralPath $TargetScript -PathType Leaf)) {
    throw "启动脚本不存在：$TargetScript"
}

if ([string]::IsNullOrWhiteSpace($ShortcutDirectory) -or -not (Test-Path -LiteralPath $ShortcutDirectory -PathType Container)) {
    throw "快捷方式目录不存在：$ShortcutDirectory"
}

$shortcutPath = Join-Path $ShortcutDirectory $ShortcutName
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $powershell
$shortcut.Arguments = '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "' + $TargetScript + '"'
$shortcut.WorkingDirectory = Split-Path -Parent $TargetScript
$shortcut.WindowStyle = 1
$shortcut.Description = 'MentorFlow 开发工作台（SSH 隧道后打开 Kuma、Beszel 和账号页）'
$shortcut.IconLocation = "$env:SystemRoot\System32\SHELL32.dll,14"
$shortcut.Save()

Write-Output "已创建桌面快捷方式：$shortcutPath"
