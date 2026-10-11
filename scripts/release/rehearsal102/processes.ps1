$taskRoot=if($env:MF_UAT_ROOT){$env:MF_UAT_ROOT}else{'D:/MentorFlow/.local/test-tmp/inapp102c'}
$env:LOCALAPPDATA=Join-Path $taskRoot 'local'
$env:TEMP=Join-Path $taskRoot 'temp';$env:TMP=$env:TEMP
$taskRoot=[IO.Path]::GetFullPath($taskRoot).TrimEnd([IO.Path]::DirectorySeparatorChar)+[IO.Path]::DirectorySeparatorChar
$rows=@(Get-CimInstance Win32_Process | Where-Object {$_.ExecutablePath -and $_.ExecutablePath.StartsWith($taskRoot,[StringComparison]::OrdinalIgnoreCase)} | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath)
ConvertTo-Json -InputObject $rows -Compress
