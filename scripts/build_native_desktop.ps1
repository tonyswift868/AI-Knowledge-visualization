$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Release = Join-Path $Root 'release\native'
$PythonWindow = Join-Path $Root '.venv\Scripts\pythonw.exe'
$PythonDebug = Join-Path $Root '.venv\Scripts\python.exe'
$Icon = Join-Path $Release 'MentorFlow.ico'
$Exe = Join-Path $Release 'MentorFlow.exe'
$LauncherSource = Join-Path $Root 'desktop\MentorFlowLauncher.cs'

if (-not (Test-Path $PythonWindow)) { throw 'Run scripts\bootstrap.ps1 first.' }
New-Item -ItemType Directory -Path $Release -Force | Out-Null
& $PythonDebug (Join-Path $PSScriptRoot 'build_desktop_icon.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$CompilerCandidates = @(
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'),
    (Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe')
)
$Compiler = $CompilerCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Compiler) { throw 'Windows C# compiler was not found.' }
& $Compiler /nologo /target:winexe "/out:$Exe" "/win32icon:$Icon" /reference:System.Windows.Forms.dll $LauncherSource
if ($LASTEXITCODE -ne 0 -or -not (Test-Path $Exe)) { throw 'MentorFlow.exe build failed.' }

$Shell = New-Object -ComObject WScript.Shell
$Shortcut = $Shell.CreateShortcut((Join-Path $Release 'MentorFlow.lnk'))
$Shortcut.TargetPath = $Exe
$Shortcut.Arguments = ''
$Shortcut.WorkingDirectory = $Root
$Shortcut.IconLocation = "$Icon,0"
$Shortcut.Description = 'MentorFlow native local learning application'
$Shortcut.Save()

$DebugShortcut = $Shell.CreateShortcut((Join-Path $Release 'MentorFlow-Debug.lnk'))
$DebugShortcut.TargetPath = $PythonDebug
$DebugShortcut.Arguments = '-m desktop.main'
$DebugShortcut.WorkingDirectory = $Root
$DebugShortcut.IconLocation = "$Icon,0"
$DebugShortcut.Description = 'MentorFlow native client with debug output'
$DebugShortcut.Save()

Write-Host "Native executable: $Exe" -ForegroundColor Green
Write-Host "Native shortcut: $(Join-Path $Release 'MentorFlow.lnk')" -ForegroundColor Green
Write-Host "Debug launcher: $(Join-Path $Release 'MentorFlow-Debug.lnk')" -ForegroundColor Green
