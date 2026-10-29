<#
tools/env_doctor.ps1 —— 项目环境体检 + 原位修复。

为什么需要它：`.venv` 是从某台机器上那个 Python 复制过来的，pyvenv.cfg 里的
home 指向 `C:\Users\OneZeroNine\...\WindowsApps\...`，本机根本没有这个路径。
venv 的 python.exe 只是转发器，找不到宿主就报
    No Python at '"C:\Users\...\python.exe'      （退出码 103）
而旧脚本只判断 `.venv\Scripts\python.exe` **存不存在**，于是每一步都显示
「虚拟环境已就绪」，后端窗口一闪即退，浏览器永远打不开。

修复用 `python -m venv --upgrade`：只重写 pyvenv.cfg 和 python.exe 转发器，
**不动 Lib\site-packages**，保留已安装的后端依赖。
只有显式 -Rebuild 才重建；旧 .venv 先改名备份，不直接删除。

退出码：0 环境可用 / 2 修不好（需人工） / 3 本机没有可用的 Python 3.11+
#>
[CmdletBinding()]
param(
    [string]$Root = '',
    [switch]$Rebuild,
    [switch]$CheckFrontend,
    [switch]$CheckOnly
)

$ErrorActionPreference = 'Continue'
if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
$Root = [IO.Path]::GetFullPath($Root)
if (-not (Test-Path -LiteralPath (Join-Path $Root 'backend\main.py'))) {
    Write-Host '[失败] Root 不是本项目源码目录。'; exit 2
}
$venv   = Join-Path $Root '.venv'
$venvPy = Join-Path $venv 'Scripts\python.exe'
# 按代码里真实的 import 点核对，不多装：
#   fastapi / uvicorn / websockets —— main.py 的 REST + /ws 双通道（websockets 由 uvicorn[standard] 带）
#   httpx                          —— core/ai_plan.py
# cv2 / pyrealsense2 是相机附加依赖：core/cameras.py 用 try/except 兜住了，
# 缺了只是相机页不可用，项目照样能打开（见 backend/requirements-vision.txt）。
$Modules  = @('fastapi', 'pydantic', 'uvicorn', 'httpx', 'websockets', 'numpy')
$Requirements = Join-Path $Root 'backend\requirements.txt'
$Mirrors = @('', 'https://pypi.tuna.tsinghua.edu.cn/simple')

function Write-Tip([string]$m) { Write-Host "      $m" }
function Test-Python([string]$Exe, [string]$Code) {
    if (-not (Test-Path -LiteralPath $Exe)) {
        return [pscustomobject]@{ Exit = 9009; Text = '文件不存在' }
    }
    $out = & $Exe '-I' '-B' '-X' 'utf8' '-c' $Code 2>&1 | Out-String
    [pscustomobject]@{ Exit = $LASTEXITCODE; Text = ($out -replace '\s+$', '') }
}

function Test-BasePython([string]$Exe, [string[]]$Pre) {
    # 能打印 "Python 3.x" 且退出码 0 才算真 Python（WindowsApps 的商店占位符不算）
    $out = & $Exe @($Pre + @('--version')) 2>&1 | Out-String
    ($LASTEXITCODE -eq 0) -and ($out -match 'Python 3\.(1[1-9]|[2-9]\d)')
}

function Find-BasePython {
    foreach ($c in @(Get-Command python.exe, python3.exe, py.exe -CommandType Application -ErrorAction SilentlyContinue)) {
        if ($c.Name -eq 'py.exe') {
            if (Test-BasePython $c.Source @('-3')) { return @{ Exe = $c.Source; Pre = @('-3') } }
        } elseif (Test-BasePython $c.Source @()) { return @{ Exe = $c.Source; Pre = @() } }
    }
    $dirs = @("$env:LOCALAPPDATA\Programs\Python", 'C:\Program Files', 'D:\Program Files',
              'C:\Program Files (x86)', 'D:\', 'C:\')
    foreach ($d in $dirs) {
        if (-not (Test-Path -LiteralPath $d)) { continue }
        foreach ($dir in @(Get-ChildItem -LiteralPath $d -Directory -Filter 'Python3*' -ErrorAction SilentlyContinue)) {
            $exe = Join-Path $dir.FullName 'python.exe'
            if ((Test-Path -LiteralPath $exe) -and (Test-BasePython $exe @())) {
                return @{ Exe = $exe; Pre = @() }
            }
        }
    }
    return $null
}

function Get-VenvCfgHome {
    $cfg = Join-Path $venv 'pyvenv.cfg'
    if (-not (Test-Path -LiteralPath $cfg)) { return $null }
    $line = (Get-Content -LiteralPath $cfg -Encoding UTF8) |
            Where-Object { $_ -match '^\s*home\s*=' } | Select-Object -First 1
    if (-not $line) { return $null }
    ($line -split '=', 2)[1].Trim()
}

Write-Host "[环境体检] $Root"

# ---------- 1. 先看现在的 .venv 能不能用 ----------
$runtimeOk = (Test-Path -LiteralPath $venvPy) -and (Test-BasePython $venvPy @())
$probe = Test-Python $venvPy ("import " + ($Modules -join ','))
if ($Rebuild -and -not $CheckOnly) { $probe = [pscustomobject]@{ Exit = 1; Text = '显式请求重建虚拟环境' } }
if (-not $runtimeOk) { $probe = [pscustomobject]@{ Exit = 1; Text = '虚拟环境解释器缺失、损坏或低于 Python 3.11' } }
if ($probe.Exit -eq 0) {
    Write-Host "      虚拟环境可用：$(& $venvPy -V 2>&1)"
} else {
    Write-Host "      虚拟环境不可用（退出码 $($probe.Exit)）："
    ($probe.Text -split "`n" | Where-Object { $_ } | Select-Object -First 3) | ForEach-Object { Write-Tip $_ }

    if ($CheckOnly) { Write-Host '[只检查] 未修复环境或安装依赖。'; exit 2 }
}

# ---------- 2. 需要一个本机可用的基础 Python ----------
if ($probe.Exit -ne 0) {
    $base = $null
    $cfgHome = Get-VenvCfgHome
    if ($cfgHome) {
        $cand = Join-Path $cfgHome 'python.exe'
        if ((Test-Path -LiteralPath $cand) -and (Test-BasePython $cand @())) { $base = @{ Exe = $cand; Pre = @() } }
    }
    if (-not $base) { $base = Find-BasePython }
    if (-not $base) {
        Write-Host "[失败] 本机找不到可用的 Python 3.11+（PATH / Program Files 都翻过了）。"
        Write-Host "       装一个 Python 3.11 或 3.12 并勾选 Add to PATH，再重跑本脚本。"
        exit 3
    }
    Write-Tip "基础解释器：$($base.Exe) $($base.Pre -join ' ')"

    # ---------- 3. 原位修复（保 site-packages）或显式重建 ----------
    if ($Rebuild -and (Test-Path -LiteralPath $venv)) {
        $resolvedVenv = [IO.Path]::GetFullPath($venv)
        $venvItem = Get-Item -LiteralPath $venv -Force
        if ((Split-Path -Leaf $resolvedVenv) -ne '.venv' -or
            -not [string]::Equals((Split-Path -Parent $resolvedVenv), $Root.TrimEnd('\'), [StringComparison]::OrdinalIgnoreCase) -or
            ($venvItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            Write-Host '[失败] 虚拟环境目标不安全，未重建。'; exit 2
        }
        $backup = Join-Path $Root ('.venv.backup-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N'))
        try { Move-Item -LiteralPath $resolvedVenv -Destination $backup -ErrorAction Stop }
        catch { Write-Host "[失败] 备份虚拟环境失败：$_"; exit 2 }
        Write-Host "[重建] 旧环境已保留：$backup"
        $runtimeOk = $false
    }
    if (-not $runtimeOk -and (Test-Path -LiteralPath (Join-Path $venv 'pyvenv.cfg'))) {
        Write-Host "[修复] python -m venv --upgrade .venv（不重装依赖）..."
        & $base.Exe @($base.Pre + @('-m', 'venv', '--upgrade', $venv)) 2>&1 | Out-String | ForEach-Object { if ($_) { Write-Tip $_ } }
        if ($LASTEXITCODE -ne 0) { Write-Host '[失败] 虚拟环境原位修复失败。'; exit 2 }
    } elseif (-not $runtimeOk) {
        Write-Host "[创建] python -m venv .venv ..."
        & $base.Exe @($base.Pre + @('-m', 'venv', $venv)) 2>&1 | Out-String | ForEach-Object { if ($_) { Write-Tip $_ } }
        if ($LASTEXITCODE -ne 0) { Write-Host '[失败] 创建虚拟环境失败。'; exit 2 }
    }

    if (-not (Test-BasePython $venvPy @())) { Write-Host '[失败] 修复后仍没有可用的 Python 3.11+。'; exit 2 }

    $probe = Test-Python $venvPy ("import " + ($Modules -join ','))
    if ($probe.Exit -ne 0) {
        # 缺包或 venv 里没有 pip：先 ensurepip，再按镜像表逐个试装
        Write-Tip "仍缺依赖，按 backend/requirements.txt 补装。"
        & $venvPy '-m' 'ensurepip' '--upgrade' 2>&1 | Out-Null
        $installed = $false
        foreach ($mir in $Mirrors) {
            $a = @('-m', 'pip', 'install', '--disable-pip-version-check', '--no-input')
            if ($mir) { $a += @('-i', $mir) }
            $a += @('-r', $Requirements)
            & $venvPy @a 2>&1 | Select-Object -Last 4 | ForEach-Object { Write-Tip $_ }
            $installCode = $LASTEXITCODE
            $probe = Test-Python $venvPy ("import " + ($Modules -join ','))
            if ($installCode -eq 0 -and $probe.Exit -eq 0) { $installed = $true; break }
            Write-Tip "（这一轮没成，换下一个源 / 检查网络）"
        }
        if (-not $installed) {
            Write-Host "[失败] 修完仍不可用："
            ($probe.Text -split "`n" | Where-Object { $_ } | Select-Object -First 5) | ForEach-Object { Write-Tip $_ }
            Write-Host "       可以备份旧环境后重建：powershell -File tools\env_doctor.ps1 -Rebuild"
            exit 2
        }
    }
    Write-Host "      环境已就绪：$(& $venvPy -V 2>&1)"
}

# ---------- 4.（可选）前端依赖与产物状态，只报告不动手 ----------
if ($CheckFrontend) {
    $web = Join-Path $Root 'web'
    $node = Get-Command node -ErrorAction SilentlyContinue
    Write-Tip ("node     : " + $(if ($node) { & node -v 2>&1 } else { '缺失：装 Node 18+ 并加入 PATH' }))
    Write-Tip ("node_modules : " + (Test-Path -LiteralPath (Join-Path $web 'node_modules')))
    Write-Tip ("dist\index.html : " + (Test-Path -LiteralPath (Join-Path $web 'dist\index.html')) + '（启动按源码变化增量构建）')
}
Write-Host "ENV_OK"
exit 0
