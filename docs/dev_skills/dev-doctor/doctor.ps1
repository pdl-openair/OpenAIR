<#
.SYNOPSIS
  OpenAIR 新人环境检测与一键修复脚本（Windows / PowerShell）。

.DESCRIPTION
  1) 检测 git / Node>=18 / pnpm>=9 / Python>=3.12 / 已装的 IDE
  2) 缺系统级依赖时仅打印 winget 命令，不替用户跑
  3) 询问 Y/n 后批量执行项目级修复（pnpm install / 后端 venv / 同步 dev_skills）
  4) 打印 IDE 启动指引和后续启动命令

.EXAMPLE
  setup.bat              (Windows 推荐双击)
  powershell -ExecutionPolicy Bypass -File docs\dev_skills\doctor\doctor.ps1
  powershell -ExecutionPolicy Bypass -File docs\dev_skills\doctor\doctor.ps1 -Yes
#>

[CmdletBinding()]
param(
  [switch]$Yes
)

$ErrorActionPreference = 'Stop'
# 脚本位于 docs/dev_skills/doctor/，仓库根需上溯 3 层
$RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '..\..\..')
Set-Location $RepoRoot

function Write-Section($title) {
  Write-Host ""
  Write-Host "=== $title ===" -ForegroundColor Cyan
}

function Test-Cmd($name) {
  return [bool](Get-Command $name -ErrorAction SilentlyContinue)
}

function Get-CmdVersion {
  param([string]$cmd, [string[]]$argList, [string]$regex)
  try {
    $out = & $cmd @argList 2>$null
    if ($out -match $regex) { return $Matches[1] }
  } catch {}
  return $null
}

function Compare-VersionGE($actual, $required) {
  if (-not $actual) { return $false }
  try {
    $a = [version]($actual -replace '^v','')
    $r = [version]$required
    return $a -ge $r
  } catch { return $false }
}

# ============ 1/4 检测 ============
Write-Section "1/4 环境检测"

$missing = @()
$hints = @()

# git
if (Test-Cmd git) {
  $v = Get-CmdVersion 'git' @('--version') 'git version (\d+\.\d+\.\d+)'
  Write-Host "[OK]  git $v"
} else {
  Write-Host "[NO]  git"
  $missing += 'git'
  $hints += '  缺 git: winget install --id Git.Git -e --source winget'
}

# Node >= 18
$nodeOk = $false
if (Test-Cmd node) {
  $v = Get-CmdVersion 'node' @('--version') '^v?(\d+\.\d+\.\d+)'
  if (Compare-VersionGE $v '18.0.0') {
    Write-Host "[OK]  Node $v"
    $nodeOk = $true
  } else {
    Write-Host "[NO]  Node $v (需 >=18)"
  }
} else {
  Write-Host "[NO]  Node"
}
if (-not $nodeOk) {
  $missing += 'node'
  $hints += '  缺 Node>=18: winget install OpenJS.NodeJS.LTS'
}

# pnpm >= 9
$pnpmOk = $false
if (Test-Cmd pnpm) {
  $v = Get-CmdVersion 'pnpm' @('--version') '^(\d+\.\d+\.\d+)'
  if (Compare-VersionGE $v '9.0.0') {
    Write-Host "[OK]  pnpm $v"
    $pnpmOk = $true
  } else {
    Write-Host "[NO]  pnpm $v (需 >=9)"
  }
} else {
  Write-Host "[NO]  pnpm"
}
if (-not $pnpmOk) {
  $missing += 'pnpm'
  $hints += '  缺 pnpm>=9: npm install -g pnpm@9.15.0  (装完 Node 再跑)'
}

# Python >= 3.12
$pyCmd = $null
foreach ($c in @('python','python3','py')) {
  if (Test-Cmd $c) { $pyCmd = $c; break }
}
$pyOk = $false
if ($pyCmd) {
  $v = Get-CmdVersion $pyCmd @('--version') 'Python (\d+\.\d+\.\d+)'
  if (Compare-VersionGE $v '3.12.0') {
    Write-Host "[OK]  Python $v ($pyCmd)"
    $pyOk = $true
  } else {
    Write-Host "[NO]  Python $v (需 >=3.12)"
  }
} else {
  Write-Host "[NO]  Python"
}
if (-not $pyOk) {
  $missing += 'python'
  $hints += '  缺 Python>=3.12: winget install Python.Python.3.12'
}

# IDE：检测命令是否存在（弱检测）
$ideHits = @()
foreach ($ide in @('claude','cursor','qoder','trae')) {
  if (Test-Cmd $ide) { $ideHits += $ide }
}
if ($ideHits.Count -gt 0) {
  Write-Host ("[OK]  IDE: {0}" -f ($ideHits -join ', '))
} else {
  Write-Host "[??]  IDE: 未在 PATH 中检出 claude/cursor/qoder/trae（如已通过桌面应用安装可忽略）"
}

if ($missing.Count -gt 0) {
  Write-Host ""
  Write-Host "存在缺失依赖，请复制以下命令执行：" -ForegroundColor Yellow
  $hints | ForEach-Object { Write-Host $_ }
  Write-Host ""
  Write-Host "(命令装完后请重开 PowerShell 让 PATH 生效，再次运行本脚本)" -ForegroundColor Yellow
  exit 1
}

# ============ 2/4 项目级修复 ============
Write-Section "2/4 项目级修复"

$apiVenv = Join-Path $RepoRoot 'services\api\.venv'
$apiRoot = Join-Path $RepoRoot 'services\api'
$venvPython = Join-Path $apiVenv 'Scripts\python.exe'
$nodeModules = Join-Path $RepoRoot 'node_modules'

$backendEnvReady = $false
if (Test-Path $venvPython) {
  & $venvPython -c "import sys, psycopg, llm_core; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" 2>$null
  $backendEnvReady = ($LASTEXITCODE -eq 0)
}

$plan = @()
if (-not (Test-Path $nodeModules)) { $plan += 'pnpm install' }
if (-not $backendEnvReady)         { $plan += "重建 services/api/.venv  +  pip install -r requirements-local.txt  +  psycopg/llm_core 自检" }
$plan += "$pyCmd docs/dev_skills/sync_dev_skills.py --target all  (幂等)"

Write-Host "将依次执行（仅写仓库内文件，可用 git restore / 删 .venv 回滚）:"
$plan | ForEach-Object { Write-Host "  - $_" }
Write-Host ""

if (-not $Yes) {
  $ans = Read-Host "是否执行？[Y/n]"
  if ($ans -and ($ans.Trim().ToLower() -ne 'y')) {
    Write-Host "已跳过修复。可稍后再次运行本脚本。"
    exit 0
  }
}

if (-not (Test-Path $nodeModules)) {
  Write-Host "`n>>> pnpm install" -ForegroundColor Green
  pnpm install
}
if (-not $backendEnvReady) {
  Write-Host "`n>>> 重建后端 venv、安装完整本地依赖并自检" -ForegroundColor Green
  if (Test-Path $apiVenv) {
    Remove-Item -Recurse -Force $apiVenv
  }
  & $pyCmd -m venv $apiVenv
  if ($LASTEXITCODE -ne 0) { throw "创建后端 venv 失败" }
  $venvPython = Join-Path $apiVenv 'Scripts\python.exe'
  Push-Location $apiRoot
  try {
    & $venvPython -m pip install -r requirements-local.txt
    if ($LASTEXITCODE -ne 0) { throw "安装后端依赖失败" }
    & $venvPython -c "import sys, psycopg, llm_core; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"
    if ($LASTEXITCODE -ne 0) { throw "后端依赖自检失败：需要 Python>=3.12，且 psycopg / llm_core 必须可导入" }
  } finally {
    Pop-Location
  }
}
Write-Host "`n>>> 同步 dev_skills 到 .claude/.cursor/.qoder/.trae" -ForegroundColor Green
& $pyCmd (Join-Path $RepoRoot 'docs\dev_skills\sync_dev_skills.py') --target all

# ============ 3/4 IDE 启动指引 ============
Write-Section "3/4 IDE 启动指引"
if ($ideHits.Count -gt 0) {
  Write-Host ("已检出 IDE: {0}" -f ($ideHits -join ', '))
  Write-Host "在仓库根目录用任一 IDE 打开本项目，即可加载 12 个 dev skills。"
} else {
  Write-Host "未在 PATH 检出 IDE。如还没装，可任选一家："
  Write-Host "  Claude Code : https://docs.claude.com/claude-code"
  Write-Host "  Cursor      : https://www.cursor.com/"
  Write-Host "  Qoder       : https://qoder.com/"
  Write-Host "  Trae        : https://www.trae.ai/"
  Write-Host "桌面 IDE 已装但未注册到 PATH 也能用，直接打开仓库目录即可。"
}

# ============ 4/4 启动命令小卡片 ============
Write-Section "4/4 启动命令小卡片"
Write-Host @"
启动后端（新开一个终端）:
  cd services/api
  .venv\Scripts\activate
  uvicorn app.main:app --reload --port 8000

启动前端（新开一个终端）:
  pnpm dev:web

完整指引: docs/GETTING_STARTED.md
"@

Write-Host ""
Write-Host "doctor 完成。" -ForegroundColor Green
