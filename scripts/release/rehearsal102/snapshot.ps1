param([string]$Out)
$taskRoot=if($env:MF_UAT_ROOT){$env:MF_UAT_ROOT}else{'D:/MentorFlow/.local/test-tmp/inapp102c'}
$env:LOCALAPPDATA=Join-Path $taskRoot 'local'
$env:TEMP=Join-Path $taskRoot 'temp';$env:TMP=$env:TEMP
$items=@()
foreach($base in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('StartMenu'))){
 Get-ChildItem -LiteralPath $base -Recurse -Filter '*MentorFlow*.lnk' -ErrorAction SilentlyContinue | ForEach-Object {
  $shell=New-Object -ComObject WScript.Shell;$link=$shell.CreateShortcut($_.FullName)
  $items+=@{path=$_.FullName;sha256=(Get-FileHash -LiteralPath $_.FullName).Hash;target=$link.TargetPath;arguments=$link.Arguments}
 }
}
$registry=@()
Get-ChildItem 'HKCU:/Software/Microsoft/Windows/CurrentVersion/Uninstall' | Where-Object {$_.PSChildName -like '*MentorFlow*'} | ForEach-Object {
 $p=Get-ItemProperty -LiteralPath $_.PSPath
 $registry+=@{key=$_.PSChildName;display=$p.DisplayName;version=$p.DisplayVersion;location=$p.InstallLocation}
}
@{shortcuts=$items;registry=$registry} | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $Out -Encoding utf8
