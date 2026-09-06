# Installer-stage source only. Daily startup executes the native onedir gate,
# never this PowerShell script. Invoke after protected full activation succeeds.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$InstallRoot,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{64}$')][string]$ManifestSha256,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$SourceSha,
    [Parameter(Mandatory=$true)][ValidatePattern('^[0-9a-f]{40}$')][string]$TreeSha,
    [string]$ShortcutPath = (Join-Path $env:PUBLIC 'Desktop\MentorFlow.lnk')
)
$ErrorActionPreference = 'Stop'
$env:PSModulePath = Join-Path $PSHOME 'Modules'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -Force
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1') -Force
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Shortcut publication is an elevated installation operation.'
}
$root = [IO.Path]::GetFullPath($InstallRoot)
$gate = Join-Path $root 'App\MentorFlowInstallGate.exe'
if (-not (Test-Path -LiteralPath $gate -PathType Leaf)) { throw 'Integrated native gate is missing.' }

# Bootstrap cannot execute the program to ask whether that same program is
# trusted. This installer-only preflight uses Windows/.NET data APIs and full
# manifest hashes before the first child process. Daily startup is unchanged.
function Assert-InstallEntry([string]$Path, [bool]$ParentOnly = $false) {
    $item = Get-Item -LiteralPath $Path -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Bootstrap reparse path rejected.' }
    $acl = Get-Acl -LiteralPath $Path
    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    $privileged = @('S-1-5-18', 'S-1-5-32-544')
    if ($owner -notin $privileged -and -not ($ParentOnly -and $owner -match '^S-1-5-21-(\d+-){3}500$')) {
        throw 'Bootstrap ordinary-user owner rejected.'
    }
    if (-not $ParentOnly -and -not $acl.AreAccessRulesProtected) { throw 'Bootstrap unprotected DACL rejected.' }
    $seen = @{}
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        $sid = $rule.IdentityReference.Value
        $mask = [int64]$rule.FileSystemRights
        if ($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow) { throw 'Bootstrap unsupported ACL rule.' }
        $inheritOnly = ($rule.PropagationFlags -band [Security.AccessControl.PropagationFlags]::InheritOnly) -ne 0
        if ($ParentOnly) {
            $unsafe = 0x100C0040
            if ($Path.TrimEnd('\') -ne ([IO.Path]::GetPathRoot($Path)).TrimEnd('\')) { $unsafe = $unsafe -bor 0x10000 }
            if (-not $inheritOnly -and $sid -notin $privileged -and ($mask -band $unsafe)) {
                throw 'Bootstrap parent permits installation replacement.'
            }
        } else {
            if ($inheritOnly -or $sid -notin @('S-1-5-18', 'S-1-5-32-544', 'S-1-5-32-545')) { throw 'Bootstrap unexpected installation ACE.' }
            $expected = if ($sid -eq 'S-1-5-32-545') { 0x1200A9 } else { 0x1F01FF }
            if ($mask -ne $expected) { throw 'Bootstrap installation write policy rejected.' }
            $seen[$sid] = $true
        }
    }
    if (-not $ParentOnly -and $seen.Count -ne 3) { throw 'Bootstrap incomplete installation ACL.' }
}
$drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($root))
if ($drive.DriveFormat -cne 'NTFS' -or $root.TrimEnd('\') -eq $drive.Name.TrimEnd('\')) { throw 'Dedicated NTFS installation required.' }
$parent = [IO.Directory]::GetParent($root)
while ($null -ne $parent) {
    Assert-InstallEntry $parent.FullName $true
    $parent = $parent.Parent
}
Assert-InstallEntry $root
$queue = [Collections.Generic.Queue[string]]::new()
$queue.Enqueue($root)
$files = @{}
while ($queue.Count) {
    $directory = $queue.Dequeue()
    foreach ($item in ([IO.DirectoryInfo]::new($directory)).GetFileSystemInfos()) {
        Assert-InstallEntry $item.FullName
        if ($item.Attributes -band [IO.FileAttributes]::Directory) {
            $queue.Enqueue($item.FullName)
        } else {
            $relative = $item.FullName.Substring($root.TrimEnd('\').Length + 1).Replace('\','/')
            $files[$relative] = $item.FullName
        }
    }
}
$manifestPath = Join-Path $root 'App\SELF-CHECK\package-manifest.json'
if ((Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $ManifestSha256) { throw 'Bootstrap manifest pin mismatch.' }
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
if ($manifest.schema -cne 'mentorflow.windows-package-manifest.v1' -or $manifest.product -cne 'MentorFlow') { throw 'Bootstrap manifest schema mismatch.' }
$expectedFiles = @{'installation-receipt.json'=$true; 'App/SELF-CHECK/package-manifest.json'=$true}
foreach ($entry in $manifest.files) {
    $relative = [string]$entry.path
    if ($relative -match '(^/|\\|:|(^|/)\.\.?(/|$))' -or [string]::IsNullOrWhiteSpace($relative)) { throw 'Bootstrap manifest path rejected.' }
    $key = 'App/' + $relative
    if ($expectedFiles.ContainsKey($key) -or -not $files.ContainsKey($key)) { throw 'Bootstrap duplicate/missing payload.' }
    $expectedFiles[$key] = $true
    if ((Get-Item -LiteralPath $files[$key]).Length -ne $entry.size -or (Get-FileHash -LiteralPath $files[$key] -Algorithm SHA256).Hash.ToLowerInvariant() -cne $entry.sha256) { throw 'Bootstrap payload hash mismatch.' }
}
if ($expectedFiles.Count -ne $files.Count -or -not $files.ContainsKey('installation-receipt.json')) { throw 'Bootstrap unknown/missing installation file.' }
$bindings = '--install-root "' + $root + '" --manifest-sha256 ' + $ManifestSha256 + ' --source-sha ' + $SourceSha + ' --tree-sha ' + $TreeSha
$process = Start-Process -FilePath $gate -ArgumentList ('gate ' + $bindings) -WindowStyle Hidden -PassThru -Wait
if ($process.ExitCode -ne 0) { throw 'Protected gate rejected installation; no shortcut was published.' }
$shell = New-Object -ComObject WScript.Shell
$shortcut = $null
try {
    $shortcut = $shell.CreateShortcut([IO.Path]::GetFullPath($ShortcutPath))
    $shortcut.TargetPath = $gate
    $shortcut.Arguments = 'launch ' + $bindings
    $shortcut.WorkingDirectory = Join-Path $root 'App'
    $shortcut.IconLocation = (Join-Path $root 'App\MentorFlow.exe') + ',0'
    $shortcut.Description = 'MentorFlow'
    $shortcut.WindowStyle = 1
    $shortcut.Save()
} finally {
    if ($null -ne $shortcut) { [Runtime.InteropServices.Marshal]::FinalReleaseComObject($shortcut) | Out-Null }
    [Runtime.InteropServices.Marshal]::FinalReleaseComObject($shell) | Out-Null
}
