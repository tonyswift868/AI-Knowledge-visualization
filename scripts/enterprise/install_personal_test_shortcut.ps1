[CmdletBinding()]
param([string]$Release = 'E:\Downloads\MentorFlow-Login-Final-20260906')
$ErrorActionPreference='Stop'
$Release=[IO.Path]::GetFullPath($Release)
$launcher=Join-Path $Release 'Start-MentorFlow.ps1'
$icon=Join-Path $Release 'MentorFlow\MentorFlow.exe'
foreach($path in @($launcher,$icon)) { if(-not(Test-Path -LiteralPath $path -PathType Leaf)){throw "Missing release entry: $path"} }
$desktop=[Environment]::GetFolderPath('Desktop')
$shortcutPath=Join-Path $desktop 'MentorFlow.lnk'
$expectedArguments='-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "'+$launcher+'"'
$shell=New-Object -ComObject WScript.Shell
$shortcut=$shell.CreateShortcut($shortcutPath)
if(Test-Path -LiteralPath $shortcutPath) {
    $expected=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    if($shortcut.TargetPath -ine $expected -or $shortcut.Arguments -cne $expectedArguments) {
        throw 'A different MentorFlow shortcut already exists; preserve it and resolve ownership first.'
    }
}
$shortcut.TargetPath=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$shortcut.Arguments=$expectedArguments
$shortcut.WorkingDirectory=$Release
$shortcut.IconLocation=$icon+',0'
$shortcut.Description='MentorFlow — verified personal test launch; isolated account data'
$shortcut.WindowStyle=7
$shortcut.Save()
[pscustomobject]@{ Shortcut=$shortcutPath; Target=$shortcut.TargetPath; Arguments=$shortcut.Arguments; WorkingDirectory=$shortcut.WorkingDirectory; Icon=$shortcut.IconLocation } | ConvertTo-Json
