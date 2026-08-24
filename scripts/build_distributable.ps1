param(
    [switch]$ValidateCleanupOnly,
    [string]$CleanupRoot,
    [string]$CleanupPath
)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $Root '.venv\Scripts\python.exe'
$Release = Join-Path $Root 'release\distributable'
$Package = Join-Path $Release 'MentorFlow'
$Work = Join-Path $Root 'tmp\pyinstaller-work'
$Spec = Join-Path $Root 'MentorFlow.spec'
$VersionFile = Join-Path $Root 'mentorflow-version.json'
$Verifier = Join-Path $PSScriptRoot 'verify_windows_package.py'

function Remove-GeneratedPath {
    param([string]$Path, [string]$Label, [string]$ProjectRoot)
    $rootFull = [IO.Path]::GetFullPath($ProjectRoot)
    $pathFull = [IO.Path]::GetFullPath($Path)
    $prefix = $rootFull.TrimEnd('\') + '\'
    if ($pathFull -eq $rootFull -or -not $pathFull.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label is outside the project root: $pathFull"
    }

    function Assert-NoReparsePath {
        param([string]$Candidate, [string]$CandidateLabel)
        $current = [IO.Path]::GetFullPath($Candidate)
        $anchor = [IO.Path]::GetPathRoot($current)
        while ($true) {
            try {
                $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
            } catch [System.Management.Automation.ItemNotFoundException] {
                $item = $null
            }
            if ($null -ne $item) {
                $attributes = [IO.FileAttributes]$item.Attributes
                $linkType = if ($item.PSObject.Properties.Name -contains 'LinkType') { [string]$item.LinkType } else { '' }
                if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $linkType) {
                    throw "$CandidateLabel traverses a symlink/reparse point: $current"
                }
            }
            if ($current -ieq $anchor) { break }
            $parent = [IO.Path]::GetDirectoryName($current)
            if ([string]::IsNullOrEmpty($parent) -or $parent -ieq $current) { break }
            $current = $parent
        }
    }

    function Assert-NoReparseTree {
        param([string]$Candidate, [string]$CandidateLabel)
        $pending = [Collections.Generic.Stack[string]]::new()
        $pending.Push([IO.Path]::GetFullPath($Candidate))
        while ($pending.Count -gt 0) {
            $current = $pending.Pop()
            $item = Get-Item -LiteralPath $current -Force -ErrorAction Stop
            $attributes = [IO.FileAttributes]$item.Attributes
            $linkType = if ($item.PSObject.Properties.Name -contains 'LinkType') { [string]$item.LinkType } else { '' }
            if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $linkType) {
                throw "$CandidateLabel contains a symlink/reparse point: $current"
            }
            if (-not $item.PSIsContainer) { continue }
            foreach ($child in @(Get-ChildItem -LiteralPath $current -Force -ErrorAction Stop)) {
                $childAttributes = [IO.FileAttributes]$child.Attributes
                $childLinkType = if ($child.PSObject.Properties.Name -contains 'LinkType') { [string]$child.LinkType } else { '' }
                if (($childAttributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or $childLinkType) {
                    throw "$CandidateLabel contains a symlink/reparse point: $($child.FullName)"
                }
                if ($child.PSIsContainer) { $pending.Push($child.FullName) }
            }
        }
    }

    # Check the root, every existing parent, the target, and the complete
    # target tree before Remove-Item can recurse into anything.  Missing
    # generated paths are safe; their existing parents are still checked.
    Assert-NoReparsePath -Candidate $rootFull -CandidateLabel 'project root'
    Assert-NoReparsePath -Candidate $pathFull -CandidateLabel $Label
    if (Test-Path -LiteralPath $pathFull) {
        Assert-NoReparseTree -Candidate $pathFull -CandidateLabel $Label
        Remove-Item -LiteralPath $pathFull -Recurse -Force
    }
}

if ($ValidateCleanupOnly) {
    if ([string]::IsNullOrWhiteSpace($CleanupRoot)) { throw 'CleanupRoot is required in validation mode.' }
    if ([string]::IsNullOrWhiteSpace($CleanupPath)) { throw 'CleanupPath is required in validation mode.' }
    Remove-GeneratedPath -Path $CleanupPath -Label 'generated cleanup validation path' -ProjectRoot $CleanupRoot
    exit 0
}

if (-not (Test-Path $Python)) { throw 'Run scripts\bootstrap.ps1 first.' }
if (-not (Test-Path $VersionFile)) { throw "Single-source version manifest is missing: $VersionFile" }
if (-not (Test-Path $Verifier)) { throw "Package verifier is missing: $Verifier" }
if (-not (Test-Path (Join-Path $Root 'release\native\MentorFlow.ico'))) {
    & $Python (Join-Path $PSScriptRoot 'build_desktop_icon.py')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

# A build must start from a known generated staging tree.  These are the only
# two disposable paths this script is permitted to remove; source and user
# data are never cleaned here.
Remove-GeneratedPath -Path $Package -Label 'generated package directory' -ProjectRoot $Root
Remove-GeneratedPath -Path $Work -Label 'PyInstaller work directory' -ProjectRoot $Root
New-Item -ItemType Directory -Path $Release -Force | Out-Null
New-Item -ItemType Directory -Path $Work -Force | Out-Null
Push-Location $Root
try {
    & $Python -m PyInstaller --noconfirm --distpath $Release --workpath $Work $Spec
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Pop-Location
}

$App = Join-Path $Release 'MentorFlow\MentorFlow.exe'
$GeneratedSelfCheckExe = Join-Path $Release 'MentorFlow\MentorFlowSelfCheck.exe'
if (-not (Test-Path $App)) { throw 'Distributable executable was not produced.' }
if (-not (Test-Path $GeneratedSelfCheckExe)) { throw 'Standalone self-check executable was not produced.' }
Copy-Item -LiteralPath (Join-Path $Root 'docs\distribution-readme.txt') `
    -Destination (Join-Path $Release 'MentorFlow\使用说明.txt') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'start_ui_review.ps1') `
    -Destination (Join-Path $Release 'MentorFlow\启动UI评审.ps1') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'start_ui_review.cmd') `
    -Destination (Join-Path $Release 'MentorFlow\启动UI评审.cmd') -Force
Copy-Item -LiteralPath $VersionFile -Destination (Join-Path $Release 'MentorFlow\mentorflow-version.json') -Force

# Keep the frozen payload and user data separate.  SELF-CHECK contains the
# auditable source helper and cmd.exe entrypoints.  The standalone console EXE
# is copied into SELF-CHECK so the whole self-check surface is self-contained.
$SelfCheck = Join-Path $Release 'MentorFlow\SELF-CHECK'
New-Item -ItemType Directory -Path $SelfCheck -Force | Out-Null
Copy-Item -LiteralPath $GeneratedSelfCheckExe -Destination (Join-Path $SelfCheck 'MentorFlowSelfCheck.exe') -Force
Remove-Item -LiteralPath $GeneratedSelfCheckExe -Force
Copy-Item -LiteralPath $Verifier -Destination (Join-Path $SelfCheck 'verify_windows_package.py') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'mentorflow-check.bat') -Destination (Join-Path $SelfCheck 'mentorflow-check.bat') -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'mentorflow-update.bat') -Destination (Join-Path $SelfCheck 'mentorflow-update.bat') -Force

& $Python $Verifier build-manifest --package-root (Join-Path $Release 'MentorFlow')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& $Python $Verifier verify --package-root (Join-Path $Release 'MentorFlow') --no-data-check --no-smoke
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$PackageVersion = (Get-Content -LiteralPath $VersionFile -Raw | ConvertFrom-Json).version
Write-Host "Distributable application: $App (version $PackageVersion)" -ForegroundColor Green
Write-Host "Package self-check: $(Join-Path $SelfCheck 'mentorflow-check.bat')" -ForegroundColor Green
