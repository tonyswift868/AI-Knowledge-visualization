[CmdletBinding()]
param([Parameter(Mandatory=$true)][string[]]$PayloadRoots,[switch]$Delete)
$ErrorActionPreference='Stop'
$archive='D:\MentorFlow-UAT-Evidence-Archive'
$project='E:\Codex\Worktrees\MF-e1-account-core-e107-final'
$approved=@('D:\MF12A\App','D:\MF12B\App','D:\MF12C\App','D:\MF13B\App',
 'D:\MFU7\base\App','D:\MFU7\base\.u\0b091741b8a4\App',
 'D:\MFU8\trial\App','D:\MFU8\profiled-base\App','D:\MFU8\cold\App','D:\MFU8\base\App','D:\MFU8\base\.u\6895e1dd4193\App',
 'D:\MF14W\release\base\App','E:\Downloads\MentorFlow-UAT-Current')
$records=[Collections.Generic.List[object]]::new()
foreach($inputPath in $PayloadRoots){
 $root=[IO.Path]::GetFullPath($inputPath).TrimEnd('\')
 $record=[ordered]@{path=$root;owner_task='E107 historical desktop qualification';size_bytes=0;contains_source=$false;contains_large_app_payload=$true;contains_only_scratch=$true;active_reference=$false;safe_to_delete=$false;status='PRESERVED';reason='';archived_files=@();deleted_bytes=0}
 try{
  if($root -notin $approved){throw 'Path not in explicit reviewed payload allowlist'}
  $item=Get-Item -LiteralPath $root -Force
  if(!$item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)){throw 'Payload root is not a plain directory'}
  $files=[Collections.Generic.List[object]]::new();$dirs=[Collections.Generic.List[string]]::new();$todo=[Collections.Generic.Stack[string]]::new();$todo.Push($root)
  while($todo.Count){
   $directory=$todo.Pop()
   foreach($entry in (Get-ChildItem -LiteralPath $directory -Force)){
    if($entry.Attributes -band [IO.FileAttributes]::ReparsePoint){throw "Reparse point preserved: $($entry.FullName)"}
    if($entry.Name -eq '.git'){throw 'Source tree preserved'}
    if($entry.PSIsContainer){$dirs.Add($entry.FullName);$todo.Push($entry.FullName)}else{$files.Add($entry);$record.size_bytes+=$entry.Length}
   }
  }
  $manifestPath=Join-Path $root 'SELF-CHECK\package-manifest.json'
  $identityPath=Join-Path $root 'SELF-CHECK\source-identity.json'
  $manifest=Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
  $identity=Get-Content -LiteralPath $identityPath -Raw -Encoding UTF8 | ConvertFrom-Json
  if($manifest.product -ne 'MentorFlow' -or $identity.source_sha -notmatch '^[0-9a-f]{40}$'){throw 'No exact MentorFlow source ownership'}
  & git -C $project cat-file -e "$($identity.source_sha)^{commit}"
  if($LASTEXITCODE){throw 'Unknown source commit'}
  $expected=[Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)
  foreach($row in $manifest.files){
   $relative=[string]$row.path
   if($relative -match '(^/|:|\\|(^|/)\.\.(/|$))'){throw 'Unsafe manifest path'}
   $expected.Add($relative.Replace('/','\')) | Out-Null
  }
  $expected.Add('SELF-CHECK\package-manifest.json') | Out-Null
  foreach($file in $files){
   $relative=$file.FullName.Substring($root.Length+1)
   $runtimeCache=$relative -match '^_internal\\.+\\__pycache__\\[^\\]+\.cpython-3[0-9]{1,2}\.pyc$'
   if(!$expected.Contains($relative) -and !$relative.StartsWith('Installer\',[StringComparison]::OrdinalIgnoreCase) -and !$runtimeCache){throw "Unknown extra file preserved: $relative"}
  }
  $active=Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase) }
  if($active){$record.active_reference=$true;throw 'Active executable reference'}
  $folderName=$root.Replace(':','').Replace('\','_')
  $dest=Join-Path $archive $folderName
  New-Item -ItemType Directory -Path $dest -Force | Out-Null
  foreach($file in $files){
   $relative=$file.FullName.Substring($root.Length+1)
   $isEvidence=($relative -like 'SELF-CHECK\*.json' -or $relative -eq 'mentorflow-version.json' -or $relative -like 'Installer\Evidence\*') -and $file.Extension -in '.json','.md','.txt','.log','.jsonl' -and $file.Length -lt 16MB
   if($isEvidence){
    $target=Join-Path $dest $relative
    New-Item -ItemType Directory -Path ([IO.Path]::GetDirectoryName($target)) -Force | Out-Null
    $pin=(Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash
    if(!(Test-Path -LiteralPath $target)){Copy-Item -LiteralPath $file.FullName -Destination $target}
    if((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $pin){throw 'Evidence archive hash mismatch'}
    $record.archived_files+=@{path=$relative;sha256=$pin}
   }
  }
  $record.safe_to_delete=$true
  if($Delete){
   # Exact collected files only; no recursive wildcard deletion or ACL changes.
   foreach($file in $files){
    $resolved=[IO.Path]::GetFullPath($file.FullName)
    if(!$resolved.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)){throw 'Delete escaped approved root'}
    try{Remove-Item -LiteralPath $resolved -ErrorAction Stop;$record.deleted_bytes+=$file.Length}
    catch{$record.reason='Some files denied or in use; no permission changes';break}
   }
   foreach($directory in ($dirs | Sort-Object Length -Descending)){
    if(!(Get-ChildItem -LiteralPath $directory -Force | Select-Object -First 1)){Remove-Item -LiteralPath $directory}
   }
   if(!(Get-ChildItem -LiteralPath $root -Force | Select-Object -First 1)){Remove-Item -LiteralPath $root;$record.status='DELETED'}else{$record.status='PARTIAL_PRESERVED'}
  }else{$record.status='VERIFIED_DRY_RUN'}
 }catch{$record.reason=$_.Exception.Message}
 $records.Add([pscustomobject]$record)
 Write-Output "$($record.status) $root $($record.deleted_bytes) $($record.reason)"
}
$receipt=Join-Path $archive ('cleanup-payload-'+[DateTime]::UtcNow.ToString('yyyyMMddTHHmmssfff')+'.json')
$records | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $receipt -Encoding utf8
Write-Output "RECEIPT=$receipt"
