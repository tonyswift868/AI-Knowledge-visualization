[CmdletBinding()]
param(
    [string]$NsisPath = 'E:\Codex\App\NSIS-3.12\makensis.exe'
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$InstallerScript = Join-Path $ProjectRoot 'installer\MentorFlow.nsi'
$TempRoot = 'E:\Codex\Temp'
$guid = [guid]::NewGuid().ToString('N')
$FixtureRoot = Join-Path $TempRoot ("MentorFlow installer fixture {0}" -f $guid)
$PayloadRoot = Join-Path $FixtureRoot 'payload with spaces'
$InstallRoot = Join-Path $FixtureRoot 'installed path with spaces'
$DataRoot = Join-Path $FixtureRoot 'approved data root with spaces'
$LocalDataRoot = Join-Path $FixtureRoot 'approved local data with spaces'
$OutputFile = Join-Path $FixtureRoot 'MentorFlow fixture setup.exe'
$UnsafeOutputFile = Join-Path $FixtureRoot 'MentorFlow unsafe-path fixture setup.exe'
$LongOutputFile = Join-Path $FixtureRoot 'MentorFlow long-path fixture setup.exe'
$UnsafeInstallRoot = Join-Path $DataRoot 'program files'
$LongInstallRoot = Join-Path $FixtureRoot ('long-install-' + ('x' * 96))
$ProductKey = "Software\MentorFlowFixture_$guid"
$UninstallKey = "Software\Microsoft\Windows\CurrentVersion\Uninstall\MentorFlowFixture_$guid"
$ShortcutFolder = "MentorFlow Fixture $guid"
$ShortcutName = 'MentorFlow Fixture.lnk'
$UninstallShortcutName = 'Uninstall MentorFlow Fixture.lnk'
$DesktopShortcutName = "MentorFlow Fixture $guid.lnk"
$UnsafeOutside = Join-Path $FixtureRoot 'outside uninstall sentinel'
$UnsafeJunction = Join-Path $InstallRoot 'unsafe junction'
$StartMenuRoot = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'
$RealRegistrySubKey = 'Software\MentorFlow'
$RealDataRoot = 'E:\MentorFlowData'
$RealLocalDataRoot = Join-Path $env:LOCALAPPDATA 'MentorFlow'

function Get-HashText([string]$Text) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($Text)
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Convert-RegistryValueForDigest($Value) {
    if ($null -eq $Value) { return '<null>' }
    if ($Value -is [byte[]]) { return [Convert]::ToBase64String($Value) }
    if ($Value -is [Array]) {
        return (($Value | ForEach-Object { [string]$_ }) -join "`u{001f}")
    }
    return [string]$Value
}

function Get-RegistryDigest([string]$SubKey) {
    $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey(
        [Microsoft.Win32.RegistryHive]::CurrentUser,
        [Microsoft.Win32.RegistryView]::Default
    )
    try {
        $root = $base.OpenSubKey($SubKey, $false)
        if ($null -eq $root) { return 'MISSING' }
        $entries = [Collections.Generic.List[string]]::new()
        function Add-RegistryKeyDigest($Key, [string]$RelativePath) {
            [void]$entries.Add("K|$RelativePath")
            foreach ($name in ($Key.GetValueNames() | Sort-Object)) {
                $kind = $Key.GetValueKind($name).ToString()
                $value = $Key.GetValue($name, $null, [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
                $encoded = Convert-RegistryValueForDigest $value
                [void]$entries.Add("V|$RelativePath|$name|$kind|$encoded")
            }
            foreach ($name in ($Key.GetSubKeyNames() | Sort-Object)) {
                $child = $Key.OpenSubKey($name, $false)
                if ($null -ne $child) {
                    try {
                        Add-RegistryKeyDigest $child (Join-Path $RelativePath $name)
                    }
                    finally {
                        $child.Dispose()
                    }
                }
            }
        }
        try {
            Add-RegistryKeyDigest $root $SubKey
        }
        finally {
            $root.Dispose()
        }
        return Get-HashText (($entries | ConvertTo-Json -Compress) + "`n")
    }
    finally {
        $base.Dispose()
    }
}

function Get-TreeDigest([string]$Path, [string[]]$ExcludedRelativePrefixes = @()) {
    if (-not (Test-Path -LiteralPath $Path)) { return 'MISSING' }
    $root = Get-Item -LiteralPath $Path -Force
    $entries = [Collections.Generic.List[string]]::new()
    function Add-TreeDigest($Item, [string]$RelativePath) {
        $normalizedRelativePath = $RelativePath.Replace('/', '\').TrimStart('.', '\')
        foreach ($prefix in $ExcludedRelativePrefixes) {
            $normalizedPrefix = $prefix.Replace('/', '\').TrimStart('.', '\').TrimEnd('\')
            if (
                $normalizedRelativePath -eq $normalizedPrefix -or
                $normalizedRelativePath.StartsWith($normalizedPrefix + '\', [StringComparison]::OrdinalIgnoreCase)
            ) {
                return
            }
        }
        $isReparse = (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0)
        $kind = if ($Item.PSIsContainer) { 'D' } elseif ($isReparse) { 'L' } else { 'F' }
        $length = if ($Item.PSIsContainer) { 0 } else { [int64]$Item.Length }
        $hash = ''
        if (-not $Item.PSIsContainer -and -not $isReparse -and $Item.Length -le 4MB) {
            try {
                $hash = (Get-FileHash -LiteralPath $Item.FullName -Algorithm SHA256 -ErrorAction Stop).Hash.ToLowerInvariant()
            }
            catch [IO.IOException] {
                # Locked files are represented by stable metadata so an active
                # MentorFlow database cannot make the fixture gate fail open.
                $hash = '<unreadable-io>'
            }
            catch {
                $hash = '<unreadable>'
            }
        }
        elseif (-not $Item.PSIsContainer -and -not $isReparse) {
            # Large model/database files are represented by metadata; hashing
            # them would make this safety gate depend on multi-gigabyte I/O.
            $hash = '<omitted-large>'
        }
        [void]$entries.Add("$kind|$RelativePath|$($Item.Attributes)|$length|$($Item.LastWriteTimeUtc.Ticks)|$hash")
        if ($Item.PSIsContainer -and -not $isReparse) {
            foreach ($child in (Get-ChildItem -LiteralPath $Item.FullName -Force | Sort-Object Name)) {
                Add-TreeDigest $child (Join-Path $RelativePath $child.Name)
            }
        }
    }
    Add-TreeDigest $root '.'
    return Get-HashText (($entries | ConvertTo-Json -Compress) + "`n")
}

function Invoke-FixtureProcess([string]$FilePath, [string[]]$Arguments) {
    $process = [Diagnostics.Process]::new()
    $process.StartInfo.FileName = $FilePath
    $process.StartInfo.UseShellExecute = $false
    if ($null -ne $process.StartInfo.ArgumentList) {
        foreach ($argument in $Arguments) {
            [void]$process.StartInfo.ArgumentList.Add($argument)
        }
    }
    else {
        $process.StartInfo.Arguments = (($Arguments | ForEach-Object {
            if ($_ -match '[\s"]') { '"' + ($_.Replace('"', '\"')) + '"' } else { $_ }
        }) -join ' ')
    }
    [void]$process.Start()
    $process.WaitForExit()
    return $process.ExitCode
}

function Assert-Condition([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

try {
    Assert-Condition (Test-Path -LiteralPath $NsisPath -PathType Leaf) 'NSIS 3.12 compiler is missing.'
    Assert-Condition (Test-Path -LiteralPath $InstallerScript -PathType Leaf) 'Installer source is missing.'
    New-Item -ItemType Directory -Path $PayloadRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $DataRoot, $LocalDataRoot -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $PayloadRoot 'MentorFlow.exe') -Value 'fixture payload' -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $PayloadRoot '使用说明.txt') -Value 'fixture instructions' -Encoding UTF8
    New-Item -ItemType Directory -Path (Join-Path $PayloadRoot 'empty payload directory') -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $DataRoot 'sentinel.txt') -Value 'must survive silent uninstall' -Encoding UTF8
    Set-Content -LiteralPath (Join-Path $LocalDataRoot 'sentinel.txt') -Value 'must survive silent uninstall' -Encoding UTF8

    $realRegistryBefore = Get-RegistryDigest $RealRegistrySubKey
    $realDataBefore = Get-TreeDigest $RealDataRoot
    # The running desktop app continuously updates its QtWebEngine cache.  It
    # is unrelated to the installer fixture and would make a whole-tree digest
    # nondeterministic.  Keep the root and all persistent data in the safety
    # digest, but exclude only this explicitly volatile cache subtree.
    $realLocalDataBefore = Get-TreeDigest $RealLocalDataRoot @('MentorFlow\cache')

    $compileArgs = @(
        '/V3',
        "/DPAYLOAD_DIR=$PayloadRoot",
        '/DPRODUCT_VERSION=0.0.0',
        '/DFILE_VERSION=0.0.0.0',
        "/DOUTPUT_FILE=$OutputFile",
        "/DPRODUCT_KEY=$ProductKey",
        "/DUNINSTALL_KEY=$UninstallKey",
        "/DSHORTCUT_FOLDER=$ShortcutFolder",
        "/DSHORTCUT_NAME=$ShortcutName",
        "/DUNINSTALL_SHORTCUT_NAME=$UninstallShortcutName",
        "/DDESKTOP_SHORTCUT_NAME=$DesktopShortcutName",
        "/DDATA_ROOT_CANONICAL=$DataRoot",
        "/DDATA_ROOT_LOCALAPPDATA=$LocalDataRoot",
        "/DINSTALL_DIR=$InstallRoot",
        '/DMAX_INSTALL_DIR_CHARS=180',
        $InstallerScript
    )
    $compileOutput = @(& $NsisPath @compileArgs 2>&1)
    if ($LASTEXITCODE -ne 0 -or ($compileOutput -match 'warning')) {
        throw 'NSIS fixture compilation failed or emitted a compiler warning.'
    }
    Assert-Condition (Test-Path -LiteralPath $OutputFile -PathType Leaf) 'Fixture installer was not produced.'

    $installExit = Invoke-FixtureProcess $OutputFile @('/S')
    Assert-Condition ($installExit -eq 0) 'Fixture silent install failed.'
    Assert-Condition (Test-Path -LiteralPath (Join-Path $InstallRoot 'Uninstall.exe') -PathType Leaf) 'Fixture install path was not created.'
    Assert-Condition (Test-Path -LiteralPath (Join-Path $StartMenuRoot "$ShortcutFolder\$ShortcutName") -PathType Leaf) 'Fixture Start Menu shortcut was not created.'
    Assert-Condition (Test-Path -LiteralPath (Join-Path $StartMenuRoot "$ShortcutFolder\$UninstallShortcutName") -PathType Leaf) 'Fixture uninstall shortcut was not created.'
    $fixtureProduct = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($ProductKey, $false)
    $fixtureUninstall = [Microsoft.Win32.Registry]::CurrentUser.OpenSubKey($UninstallKey, $false)
    Assert-Condition ($null -ne $fixtureProduct -and $null -ne $fixtureUninstall) 'Fixture registry keys were not created.'
    if ($null -ne $fixtureProduct) { $fixtureProduct.Dispose() }
    if ($null -ne $fixtureUninstall) { $fixtureUninstall.Dispose() }

    $uninstaller = Join-Path $InstallRoot 'Uninstall.exe'
    New-Item -ItemType Directory -Path $UnsafeOutside -Force | Out-Null
    Set-Content -LiteralPath (Join-Path $UnsafeOutside 'sentinel.txt') -Value 'must survive unsafe program tree' -Encoding UTF8
    $junctionOutput = @(& cmd.exe /d /c mklink /J "$UnsafeJunction" "$UnsafeOutside" 2>&1)
    Assert-Condition ($LASTEXITCODE -eq 0) ("Fixture junction creation failed: " + ($junctionOutput -join ' '))
    $unsafeExit = Invoke-FixtureProcess $uninstaller @('/S')
    Start-Sleep -Milliseconds 500
    $unsafeSentinelExists = Test-Path -LiteralPath (Join-Path $UnsafeOutside 'sentinel.txt')
    $unsafeInstallExists = Test-Path -LiteralPath $InstallRoot -PathType Container
    Write-Output "MENTORFLOW_UNSAFE_UNINSTALL exit=$unsafeExit sentinel=$unsafeSentinelExists install_root=$unsafeInstallExists"
    Assert-Condition $unsafeSentinelExists 'Unsafe install-tree uninstall followed a junction.'
    Assert-Condition $unsafeInstallExists 'Unsafe install-tree uninstall removed the program directory.'
    @(& cmd.exe /d /c rmdir "$UnsafeJunction" 2>&1) | Out-Null
    Assert-Condition ($LASTEXITCODE -eq 0 -and -not (Test-Path -LiteralPath $UnsafeJunction)) 'Fixture junction cleanup failed.'

    $uninstallExit = Invoke-FixtureProcess $uninstaller @('/S')
    Assert-Condition ($uninstallExit -eq 0) 'Fixture silent uninstall failed.'
    for ($attempt = 0; $attempt -lt 20 -and (Test-Path -LiteralPath $InstallRoot); $attempt++) {
        Start-Sleep -Milliseconds 250
    }
    Assert-Condition (-not (Test-Path -LiteralPath $InstallRoot)) 'Fixture install path was not removed.'
    Assert-Condition (-not (Test-Path -LiteralPath (Join-Path $StartMenuRoot "$ShortcutFolder\$ShortcutName"))) 'Fixture Start Menu shortcut was not removed.'
    Assert-Condition (-not (Test-Path -LiteralPath (Join-Path $StartMenuRoot "$ShortcutFolder\$UninstallShortcutName"))) 'Fixture uninstall shortcut was not removed.'
    Assert-Condition (-not (Test-Path -LiteralPath "Registry::HKEY_CURRENT_USER\$ProductKey")) 'Randomized fixture product key was not removed.'
    Assert-Condition (-not (Test-Path -LiteralPath "Registry::HKEY_CURRENT_USER\$UninstallKey")) 'Randomized fixture uninstall key was not removed.'
    Assert-Condition (Test-Path -LiteralPath (Join-Path $DataRoot 'sentinel.txt')) 'Silent uninstall deleted the canonical fixture sentinel.'
    Assert-Condition (Test-Path -LiteralPath (Join-Path $LocalDataRoot 'sentinel.txt')) 'Silent uninstall deleted the local fixture sentinel.'

    $longCompileArgs = @(
        '/V3',
        "/DPAYLOAD_DIR=$PayloadRoot",
        '/DPRODUCT_VERSION=0.0.0',
        '/DFILE_VERSION=0.0.0.0',
        "/DOUTPUT_FILE=$LongOutputFile",
        "/DPRODUCT_KEY=$ProductKey",
        "/DUNINSTALL_KEY=$UninstallKey",
        "/DSHORTCUT_FOLDER=$ShortcutFolder",
        "/DSHORTCUT_NAME=$ShortcutName",
        "/DUNINSTALL_SHORTCUT_NAME=$UninstallShortcutName",
        "/DDESKTOP_SHORTCUT_NAME=$DesktopShortcutName",
        "/DDATA_ROOT_CANONICAL=$DataRoot",
        "/DDATA_ROOT_LOCALAPPDATA=$LocalDataRoot",
        "/DINSTALL_DIR=$LongInstallRoot",
        '/DMAX_INSTALL_DIR_CHARS=180',
        $InstallerScript
    )
    $longCompileOutput = @(& $NsisPath @longCompileArgs 2>&1)
    Assert-Condition ($LASTEXITCODE -eq 0 -and -not ($longCompileOutput -match 'warning')) 'Long-path fixture compilation failed or emitted a compiler warning.'
    [void](Invoke-FixtureProcess $LongOutputFile @('/S'))
    Start-Sleep -Milliseconds 500
    Assert-Condition (-not (Test-Path -LiteralPath $LongInstallRoot)) 'Installer accepted a path beyond the compiled length budget.'
    Assert-Condition (-not (Test-Path -LiteralPath "Registry::HKEY_CURRENT_USER\$ProductKey")) 'Rejected long install path wrote a product key.'

    Assert-Condition ((Get-RegistryDigest $RealRegistrySubKey) -eq $realRegistryBefore) 'Real HKCU registry digest changed during fixture test.'
    Assert-Condition ((Get-TreeDigest $RealDataRoot) -eq $realDataBefore) 'Real E:\MentorFlowData digest changed during fixture test.'
    Assert-Condition ((Get-TreeDigest $RealLocalDataRoot @('MentorFlow\cache')) -eq $realLocalDataBefore) 'Real LOCALAPPDATA MentorFlow digest changed during fixture test.'

    $unsafeCompileArgs = @(
        '/V3',
        "/DPAYLOAD_DIR=$PayloadRoot",
        '/DPRODUCT_VERSION=0.0.0',
        '/DFILE_VERSION=0.0.0.0',
        "/DOUTPUT_FILE=$UnsafeOutputFile",
        "/DPRODUCT_KEY=$ProductKey",
        "/DUNINSTALL_KEY=$UninstallKey",
        "/DSHORTCUT_FOLDER=$ShortcutFolder",
        "/DSHORTCUT_NAME=$ShortcutName",
        "/DUNINSTALL_SHORTCUT_NAME=$UninstallShortcutName",
        "/DDESKTOP_SHORTCUT_NAME=$DesktopShortcutName",
        "/DDATA_ROOT_CANONICAL=$DataRoot",
        "/DDATA_ROOT_LOCALAPPDATA=$LocalDataRoot",
        "/DINSTALL_DIR=$UnsafeInstallRoot",
        '/DMAX_INSTALL_DIR_CHARS=180',
        $InstallerScript
    )
    $unsafeCompileOutput = @(& $NsisPath @unsafeCompileArgs 2>&1)
    Assert-Condition ($LASTEXITCODE -eq 0 -and -not ($unsafeCompileOutput -match 'warning')) 'Unsafe-path fixture compilation failed or emitted a compiler warning.'
    [void](Invoke-FixtureProcess $UnsafeOutputFile @('/S'))
    Start-Sleep -Milliseconds 500
    Assert-Condition (-not (Test-Path -LiteralPath $UnsafeInstallRoot)) 'Installer accepted a program directory nested under a data root.'
    Assert-Condition (-not (Test-Path -LiteralPath "Registry::HKEY_CURRENT_USER\$ProductKey")) 'Rejected unsafe install path wrote a product key.'
    Write-Output 'MENTORFLOW_INSTALLER_FIXTURE=PASS'
}
finally {
    if (Test-Path -LiteralPath $UnsafeJunction) {
        @(& cmd.exe /d /c rmdir "$UnsafeJunction" 2>&1) | Out-Null
    }
    if (Test-Path -LiteralPath $FixtureRoot) {
        Remove-Item -LiteralPath $FixtureRoot -Recurse -Force -ErrorAction SilentlyContinue
    }
}
