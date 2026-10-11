param([switch]$BuildOnly,[switch]$InstallOnly)
$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath('D:/MentorFlow/.local/test-tmp/dir105').TrimEnd('\')
if($BuildOnly){$items=@('freeze','web','inno','public.json','public-runtime.json','stable.json') | ForEach-Object {Join-Path $root $_}}
elseif($InstallOnly){
 if(Test-Path (Join-Path $root 'custom/MentorFlow/Maintenance/unins000.exe')){throw 'Run the preview uninstaller first'}
 $items=@('custom','foreign','setup') | ForEach-Object {Join-Path $root $_}
}
else{
 if(Test-Path (Join-Path $root 'custom/MentorFlow/Maintenance/unins000.exe')){throw 'Run the preview uninstaller before final cleanup'}
 $items=@(Get-ChildItem -LiteralPath $root -Force | Where-Object {$_.Name -notin @('report.md','screenshots')} | ForEach-Object {$_.FullName})
}
foreach($item in $items){
 $full=[IO.Path]::GetFullPath($item)
 if(-not $full.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Outside own task directory'}
 if(Test-Path -LiteralPath $full){Remove-Item -LiteralPath $full -Recurse -Force}
}
