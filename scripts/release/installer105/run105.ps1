param([ValidateSet('setup','fake-missing','fake-foreign','remove-fake','uninstall','processes')][string]$Action='setup', [string]$Label='a')
$ErrorActionPreference='Stop'
$root='D:/MentorFlow/.local/test-tmp/dir105'
$env:TEMP=Join-Path $root 'temp';$env:TMP=$env:TEMP
$env:PYTHONDONTWRITEBYTECODE='1'
$keyPath='Software\Microsoft\Windows\CurrentVersion\Uninstall\MentorFlow.UAT.dir105_is1'
if($Action -like 'fake-*' -or $Action -eq 'remove-fake'){
 $base=[Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::CurrentUser,[Microsoft.Win32.RegistryView]::Registry64)
 if($Action -eq 'remove-fake'){$base.DeleteSubKeyTree($keyPath,$false);$base.Close();exit}
 $value=Join-Path $root 'missing/MentorFlow'
 if($Action -eq 'fake-foreign'){
  $value=Join-Path $root 'foreign/MentorFlow'
  New-Item -ItemType Directory -Force $value | Out-Null
  Set-Content -LiteralPath (Join-Path $value 'not-mentorflow.txt') -Value 'Foreign directory; must not be adopted.'
 }
 if(Test-Path (Join-Path $root 'custom/MentorFlow/Maintenance/unins000.exe')){throw 'Uninstall owned installation before replacing its registry record'}
 $key=$base.CreateSubKey($keyPath)
 foreach($name in @('InstallLocation','Inno Setup: App Path')){$key.SetValue($name,$value.Replace('/','\'))}
 $key.SetValue('DisplayName','MentorFlow dir105 isolated stale record')
 $key.SetValue('DisplayVersion','0.0.0');$key.Close();$base.Close()
 Write-Output $value;exit
}
if($Action -eq 'processes'){
 Get-CimInstance Win32_Process | Where-Object {$_.ExecutablePath -and $_.ExecutablePath.StartsWith($root.Replace('/','\')+'\',[StringComparison]::OrdinalIgnoreCase)} | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath
 exit
}
if($Action -eq 'uninstall'){
 $exe=Join-Path $root 'custom/MentorFlow/Maintenance/unins000.exe'
}else{$exe=Join-Path $root 'setup/MentorFlow-Setup.exe'}
# Visible UI is intentional. No SetForegroundWindow, simulated input or server.
$p=Start-Process -FilePath $exe -ArgumentList ('/LOG="'+(Join-Path $root ('logs/'+$Label+'.log'))+'"') -PassThru
@{pid=$p.Id;action=$Action;label=$Label} | ConvertTo-Json -Compress
