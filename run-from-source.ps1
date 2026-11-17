[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$web = Join-Path $repo 'refactor\learning\web'
$requirements = Join-Path $repo 'refactor\desktop\requirements-account-shell.txt'
$venv = Join-Path $repo '.venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'
$pythonReady = Join-Path $venv '.requirements-installed'
$cache = Join-Path $repo '.source-run-cache'
$pipCache = Join-Path $cache 'pip'
$npmCache = Join-Path $cache 'npm'

function Stop-WithMessage([string]$message) {
    Write-Error $message
    exit 1
}

if (-not (Test-Path -LiteralPath (Join-Path $repo '.git'))) {
    Stop-WithMessage '请从克隆仓库根目录运行 run-from-source.ps1。'
}
if (-not (Test-Path -LiteralPath $requirements)) {
    Stop-WithMessage "找不到桌面端依赖文件：$requirements"
}

$python = Get-Command python -ErrorAction SilentlyContinue
if ($null -eq $python) {
    Stop-WithMessage '需要 Python 3.12.x（建议 3.12.10）。请从 https://www.python.org/downloads/ 安装后重试。'
}
$pythonVersion = (& $python.Source -c 'import sys; print("%d.%d" % sys.version_info[:2])').Trim()
if ($pythonVersion -ne '3.12') {
    Stop-WithMessage "需要 Python 3.12.x（当前为 $pythonVersion）。"
}

$node = Get-Command node -ErrorAction SilentlyContinue
$npm = Get-Command npm -ErrorAction SilentlyContinue
if ($null -eq $node -or $null -eq $npm) {
    Stop-WithMessage '需要 Node.js 24.x（npm 随 Node 一起安装）。请从 https://nodejs.org/ 安装后重试。'
}
$nodeMajor = (& $node.Source --version).Trim().TrimStart('v').Split('.')[0]
if ($nodeMajor -ne '24') {
    Stop-WithMessage "需要 Node.js 24.x（当前主版本为 $nodeMajor）。"
}

# These variables affect this PowerShell process and its children only.
$env:PIP_CACHE_DIR = $pipCache
$env:npm_config_cache = $npmCache
$env:MENTORFLOW_WEB_DEPS = $web
New-Item -ItemType Directory -Force -Path $cache | Out-Null

if (-not (Test-Path -LiteralPath $venvPython)) {
    & $python.Source -m venv $venv
    if ($LASTEXITCODE -ne 0) { Stop-WithMessage '创建 Python 虚拟环境失败。' }
}

if (-not (Test-Path -LiteralPath $pythonReady)) {
    & $venvPython -m pip install --upgrade pip
    if ($LASTEXITCODE -ne 0) { Stop-WithMessage '升级 pip 失败，请检查网络后重试。' }
    & $venvPython -m pip install -r $requirements
    if ($LASTEXITCODE -ne 0) { Stop-WithMessage '安装 Python 依赖失败，请检查网络后重试。' }
    New-Item -ItemType File -Force -Path $pythonReady | Out-Null
}

$nodeModules = Join-Path $web 'node_modules'
if (-not (Test-Path -LiteralPath $nodeModules)) {
    Push-Location $web
    try {
        & $npm.Source ci --no-audit --no-fund
        if ($LASTEXITCODE -ne 0) { Stop-WithMessage '安装前端依赖失败，请检查网络后重试。' }
    } finally {
        Pop-Location
    }
}

$distIndex = Join-Path $web 'dist\index.html'
if (-not (Test-Path -LiteralPath $distIndex)) {
    Push-Location $repo
    try {
        & $node.Source (Join-Path $web 'node_modules\vite\bin\vite.js') build --config (Join-Path $web 'vite.config.mjs')
        if ($LASTEXITCODE -ne 0) { Stop-WithMessage '构建前端界面失败。' }
    } finally {
        Pop-Location
    }
}

Push-Location $repo
try {
    & $venvPython -m refactor.desktop.account_shell_main
} finally {
    Pop-Location
}
