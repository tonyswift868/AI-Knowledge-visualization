$ErrorActionPreference = 'Stop'
$workbenchRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
try {
    $workbenchCandidates = @(
        $env:MENTORFLOW_DEV_PYTHON,
        (Join-Path $workbenchRoot '.venv\Scripts\python.exe'),
        'E:\Codex\Projects\MentorFlow\.venv\Scripts\python.exe',
        'E:\Codex\Temp\mf-e1-02b-verify-py313\Scripts\python.exe',
        'E:\Codex\Data\cache\uv\archive-v0\lP03bTucMtV7k8in\Scripts\python.exe'
    )
    $workbenchPython = $null
    foreach ($workbenchCandidate in $workbenchCandidates) {
        if ($workbenchCandidate -and (Test-Path -LiteralPath $workbenchCandidate -PathType Leaf)) {
            & $workbenchCandidate -c 'import PySide6, jsonschema' 2>$null
            if ($LASTEXITCODE -eq 0) { $workbenchPython = $workbenchCandidate; break }
        }
    }
    if (-not $workbenchPython) {
        throw 'No existing Python runtime with PySide6 and jsonschema. Set MENTORFLOW_DEV_PYTHON to its python.exe. See tools/dev_workbench/README.md.'
    }
    $workbenchWindowPython = Join-Path (Split-Path -Parent $workbenchPython) 'pythonw.exe'
    if (-not (Test-Path -LiteralPath $workbenchWindowPython)) { $workbenchWindowPython = $workbenchPython }
    Start-Process -FilePath $workbenchWindowPython -ArgumentList @('-m', 'tools.dev_workbench') -WorkingDirectory $workbenchRoot -WindowStyle Hidden
} catch {
    Add-Type -AssemblyName System.Windows.Forms
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'MentorFlow Dev Workbench') | Out-Null
    exit 1
}
