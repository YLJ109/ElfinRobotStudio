<#
前端依赖与增量构建。-Dev 不构建；-ForceBuild 显式重建。
退出码：0 就绪 / 3 工具缺失 / 4 安装失败 / 5 构建失败。
此文件使用 UTF-8 BOM，兼容 Windows PowerShell 5.1。
#>
[CmdletBinding()]
param(
    [string]$Root = '',
    [switch]$Dev,
    [switch]$ForceBuild
)

if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
$Root = [IO.Path]::GetFullPath($Root)
$web = Join-Path $Root 'web'
$distIndex = Join-Path $web 'dist\index.html'
$stampPath = Join-Path $Root 'output\runtime\frontend-build.json'

function Invoke-Npm([string[]]$NpmArgs) {
    # 不把退出码混进输出管道；调用 .cmd 不需要放宽 npm.ps1 的执行策略。
    $output = @(& $script:npmCmd @NpmArgs 2>&1)
    $code = $LASTEXITCODE
    $output | Select-Object -Last 12 | ForEach-Object { Write-Host "        $_" }
    return [int]$code
}

function Get-FrontendInputs([string]$Directory) {
    # 先剪枝，避免遍历 node_modules；包含 public、配置、锁文件和 .env。
    foreach ($item in Get-ChildItem -LiteralPath $Directory -Force -ErrorAction Stop) {
        if ($item.PSIsContainer -and $item.Name -in @('node_modules', 'dist', '.git', '.vite', 'coverage')) { continue }
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "构建输入包含链接，请先明确实际来源：$($item.FullName)"
        }
        if ($item.PSIsContainer) { Get-FrontendInputs $item.FullName } else { $item }
    }
}

function Get-FrontendFingerprint([string]$Directory, [object[]]$Files) {
    $rows = foreach ($file in ($Files | Sort-Object FullName)) {
        $relative = $file.FullName.Substring($Directory.TrimEnd('\', '/').Length + 1).Replace('\', '/')
        $hash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256 -ErrorAction Stop).Hash
        '{0}:{1}' -f $relative, $hash
    }
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        return [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes(($rows -join [char]10)))).Replace('-', '')
    } finally { $sha.Dispose() }
}

try {
    if (-not (Test-Path -LiteralPath (Join-Path $web 'package.json'))) { throw "找不到前端目录 $web" }
    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    $script:npmCmd = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
    if (-not $node -or -not $script:npmCmd) { throw '需要 Node.js 和 npm.cmd；桌面面板不需要 Node。' }
} catch {
    Write-Host "[失败] $($_.Exception.Message)"
    exit 3
}

$installed = $false
Push-Location -LiteralPath $web
try {
    if (-not (Test-Path -LiteralPath (Join-Path $web 'node_modules\vite\bin\vite.js'))) {
        Write-Host '[前端] 补齐依赖（优先镜像，失败后回退默认源）。'
        $installCode = 1
        foreach ($mirror in @('https://registry.npmmirror.com', '')) {
            # 使用锁文件安装，不改用户的 package-lock.json。
            $npmArgs = @('ci', '--no-audit', '--no-fund')
            if (-not (Test-Path -LiteralPath (Join-Path $web 'package-lock.json'))) {
                $npmArgs = @('install', '--package-lock=false', '--no-audit', '--no-fund')
            }
            if ($mirror) { $npmArgs += @('--registry', $mirror) }
            $installCode = Invoke-Npm $npmArgs
            if ($installCode -eq 0) { break }
            Write-Host "[前端] npm 安装退出码 $installCode，尝试下一源。"
        }
        if ($installCode -ne 0 -or -not (Test-Path -LiteralPath (Join-Path $web 'node_modules\vite\bin\vite.js'))) {
            Write-Host "[失败] 前端依赖安装失败（npm 退出码 $installCode）。"
            exit 4
        }
        $installed = $true
    }
    if ($Dev -and -not $ForceBuild) {
        Write-Host '[前端] 开发模式：Vite 热更新，不构建 dist。'
        exit 0
    }

    $files = @(Get-FrontendInputs $web)
    $fingerprint = Get-FrontendFingerprint $web $files
    $needsBuild = $ForceBuild -or $installed -or -not (Test-Path -LiteralPath $distIndex)
    if (-not $needsBuild) {
        if (Test-Path -LiteralPath $stampPath) {
            try {
                $stamp = Get-Content -LiteralPath $stampPath -Raw -Encoding UTF8 | ConvertFrom-Json -ErrorAction Stop
                $needsBuild = $stamp.inputs -ne $fingerprint -or $stamp.index -ne (Get-FileHash -LiteralPath $distIndex -Algorithm SHA256).Hash
            } catch { $needsBuild = $true }
        } else {
            # 没有内容指纹的旧产物可能只是开发端口跳转，时间戳不能证明身份。
            $needsBuild = $true
        }
    }
    if ($needsBuild) {
        Write-Host '[前端] 输入有变化、产物缺失或显式 build，开始构建。'
        $buildCode = Invoke-Npm @('run', 'build')
        if ($buildCode -ne 0 -or -not (Test-Path -LiteralPath $distIndex)) {
            Write-Host "[失败] 构建失败（npm 退出码 $buildCode）；旧 dist 不作为成功依据。"
            exit 5
        }
        # 构建时若有编辑，不能把未编译的源码记为已编译。
        $after = Get-FrontendFingerprint $web @(Get-FrontendInputs $web)
        if ($after -ne $fingerprint) { throw '构建期间前端输入发生变化，请重新 build。' }
    } else { Write-Host '[前端] 输入未变化，复用现有 dist。' }

    $builtHtml = Get-Content -LiteralPath $distIndex -Raw -Encoding UTF8
    if ($builtHtml -match '(?i)(http-equiv\s*=\s*["'']refresh|127\.0\.0\.1:5173|localhost:5173)' -or
        $builtHtml -notmatch 'assets/[^"'']+\.js') {
        throw '产物不是独立生产页面，请重新 build；不能发布开发服务跳转页。'
    }
    New-Item -ItemType Directory -Force (Split-Path -Parent $stampPath) -ErrorAction Stop | Out-Null
    @{ version = 1; inputs = $fingerprint; index = (Get-FileHash -LiteralPath $distIndex -Algorithm SHA256).Hash } |
        ConvertTo-Json | Set-Content -LiteralPath $stampPath -Encoding UTF8 -ErrorAction Stop
    Write-Host 'FRONTEND_OK'
} catch {
    Write-Host "[失败] $($_.Exception.Message)"
    exit 5
} finally { Pop-Location }
exit 0
