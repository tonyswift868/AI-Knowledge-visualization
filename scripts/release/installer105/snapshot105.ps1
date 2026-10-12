param([Parameter(Mandatory=$true)][string]$Out)
$ErrorActionPreference='Stop'
$taskRoot=if($env:MF_UAT_ROOT){$env:MF_UAT_ROOT}else{'D:/MentorFlow/.local/test-tmp/dir105'}
$env:LOCALAPPDATA=Join-Path $taskRoot 'local'
$env:TEMP=Join-Path $taskRoot 'temp';$env:TMP=$env:TEMP
$records=@()
foreach($view in @([Microsoft.Win32.RegistryView]::Registry64,[Microsoft.Win32.RegistryView]::Registry32)){
 $base=[Microsoft.Win32.RegistryKey]::OpenBaseKey([Microsoft.Win32.RegistryHive]::CurrentUser,$view)
 $uninstall=$base.OpenSubKey('Software\Microsoft\Windows\CurrentVersion\Uninstall')
 foreach($name in $uninstall.GetSubKeyNames() | Where-Object {$_ -like '*MentorFlow*'}){
  $key=$uninstall.OpenSubKey($name);$values=@{}
  foreach($n in $key.GetValueNames()){$values[$n]=$key.GetValue($n)}
  $records+=@{view=$view.ToString();key=$name;values=$values};$key.Close()
 }
 $uninstall.Close();$base.Close()
}
$links=@();$shell=New-Object -ComObject WScript.Shell
foreach($folder in @('Desktop','CommonDesktopDirectory','StartMenu','CommonStartMenu')){
 $base=[Environment]::GetFolderPath($folder)
 Get-ChildItem -LiteralPath $base -Recurse -Filter '*MentorFlow*.lnk' -ErrorAction SilentlyContinue | ForEach-Object {
  $link=$shell.CreateShortcut($_.FullName)
  $links+=@{path=$_.FullName;sha256=(Get-FileHash -LiteralPath $_.FullName).Hash;target=$link.TargetPath;arguments=$link.Arguments}
 }
}
$files=@()
Get-ChildItem -LiteralPath 'D:/MentorFlowApp' -Recurse -File | ForEach-Object {
 try {$hash=(Get-FileHash -LiteralPath $_.FullName -ErrorAction Stop).Hash} catch {$hash='IN_USE'}
 $files+=@{path=$_.FullName;size=$_.Length;sha256=$hash}
}
$default=Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'Programs/MentorFlow'
$defaultFiles=@(Get-ChildItem -LiteralPath $default -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object {$_.FullName})
@{registry=$records;shortcuts=$links;formalFiles=$files;defaultPath=$default;defaultFiles=$defaultFiles} | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $Out -Encoding utf8
@{registrations=$records.Count;shortcuts=$links.Count;formalFiles=$files.Count;defaultFiles=$defaultFiles.Count} | ConvertTo-Json -Compress
