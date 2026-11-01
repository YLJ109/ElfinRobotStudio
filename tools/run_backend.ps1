<#
后端启动、健康复用和显式停止。普通启动绝不结束已有进程。
-Ensure 后台启动并等待健康；-Restart / -Stop 只作用于可验证的本仓库进程。
保留直接前台运行、-Reload、-Root、-Port 和滚动日志功能。
-WaitOnly 供 wait_backend.ps1 使用，只观察，不启停服务。
-Web 已下线：8710 只提供 API，不再托管 web/dist；保留开关只为快速给出失败原因。
#>
[CmdletBinding()]
param(
    [string]$Root = '',
    [ValidateRange(1, 65535)][int]$Port = 8710,
    [switch]$Reload,
    [switch]$Web,
    [switch]$Stop,
    [switch]$Ensure,
    [switch]$Restart,
    [switch]$WaitOnly,
    [ValidateRange(1, 600)][int]$TimeoutSec = 30,
    [string]$Log = '',
    [int]$LauncherProcessId = 0
)

function Test-RepositoryBackend($Process, [string]$ProjectRoot, [int]$BackendPort) {
    if ($Process.Name -notin @('python.exe', 'pythonw.exe')) { return $false }
    $commandLine = [string]$Process.CommandLine
    if ($commandLine -notmatch '(?:^|\s)-m\s+"?uvicorn"?\s+"?backend\.main:app"?(?=\s|$)') { return $false }
    $portMatch = [regex]::Match($commandLine, '(?:^|\s)--port(?:\s+|=)(\d+)(?=\s|$)')
    if (-not $portMatch.Success -or [int]$portMatch.Groups[1].Value -ne $BackendPort) { return $false }
    $expected = [IO.Path]::GetFullPath((Join-Path $ProjectRoot 'backend/.venv/Scripts\python.exe'))
    $firstArg = [regex]::Match($commandLine, '^\s*(?:"([^"]+)"|(\S+))')
    $argvExe = if ($firstArg.Groups[1].Success) { $firstArg.Groups[1].Value } else { $firstArg.Groups[2].Value }
    foreach ($candidate in @($Process.ExecutablePath, $argvExe)) {
        # 相对命令行不足以证明工作目录；绝不按调用者的 cwd 猜测归属。
        if ($candidate -and [IO.Path]::IsPathRooted($candidate) -and
            [string]::Equals([IO.Path]::GetFullPath($candidate), $expected, [StringComparison]::OrdinalIgnoreCase)) {
            return $true
        }
    }
    return $false
}

function Get-BackendInventory([string]$ProjectRoot, [int]$BackendPort) {
    $processes = @(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" -ErrorAction Stop)
    $owned = @{}
    foreach ($process in $processes) {
        if (Test-RepositoryBackend $process $ProjectRoot $BackendPort) {
            $owned[[int]$process.ProcessId] = [pscustomobject]@{ Process = $process; Depth = 0 }
        }
    }
    # uvicorn --reload / Windows venv 转发器的子进程也必须追溯到已核验的父进程。
    do {
        $added = $false
        foreach ($process in $processes) {
            $parent = $owned[[int]$process.ParentProcessId]
            if (-not $owned.ContainsKey([int]$process.ProcessId) -and $parent -and
                $process.CreationDate -and $parent.Process.CreationDate -and
                $process.CreationDate -ge $parent.Process.CreationDate) {
                $owned[[int]$process.ProcessId] = [pscustomobject]@{ Process = $process; Depth = $parent.Depth + 1 }
                $added = $true
            }
        }
    } while ($added)
    $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction Stop | Where-Object { $_.LocalPort -eq $BackendPort })
    $foreign = @($listeners | Where-Object { -not $owned.ContainsKey([int]$_.OwningProcess) })
    return [pscustomobject]@{ Owned = @($owned.Values); Listeners = $listeners; Foreign = $foreign }
}

function Stop-RepositoryBackend($Inventory) {
    if ($Inventory.Foreign.Count) { throw '端口包含归属无法核验的进程，拒绝停止或重启。' }
    # 先结束监督进程，再清理由它启动的子进程，避免 reload 重新拉起子进程。
    foreach ($entry in ($Inventory.Owned | Sort-Object Depth)) {
        $expected = $entry.Process
        $current = Get-CimInstance Win32_Process -Filter "ProcessId=$($expected.ProcessId)" -ErrorAction Stop
        if (-not $current) { continue }
        if (-not $expected.CreationDate -or $current.CreationDate -ne $expected.CreationDate -or
            $current.CommandLine -cne $expected.CommandLine -or $current.ExecutablePath -cne $expected.ExecutablePath) {
            throw "PID $($expected.ProcessId) 身份已变化，停止操作已中止。"
        }
        Write-Host "[stop] 已核验本仓库 PID $($expected.ProcessId)"
        try {
            Stop-Process -Id $expected.ProcessId -Force -ErrorAction Stop
        } catch {
            # Stopping a verified parent may also terminate its child between
            # the identity check and Stop-Process. An already-exited PID is done.
            if (Get-Process -Id $expected.ProcessId -ErrorAction SilentlyContinue) { throw }
        }
    }
}

function Test-BackendHealth([int]$BackendPort) {
    $base = "http://127.0.0.1:$BackendPort"
    $stateResponse = Invoke-WebRequest "$base/api/state" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
    $toolsResponse = Invoke-WebRequest "$base/api/tools" -UseBasicParsing -TimeoutSec 2 -ErrorAction Stop
    if ($stateResponse.StatusCode -ne 200 -or $toolsResponse.StatusCode -ne 200) { throw '健康接口未返回 200。' }
    $state = $stateResponse.Content | ConvertFrom-Json -ErrorAction Stop
    $tools = $toolsResponse.Content | ConvertFrom-Json -ErrorAction Stop
    if ($state.mode -notin @('sim', 'real') -or -not $state.session_id -or -not $state.joints -or -not $tools.mounted) {
        throw '端口响应不是预期的工作站状态。'
    }
    return $true
}

function Invoke-BackendProcess([string]$Python, [string[]]$Arguments, [string]$ProjectRoot, [string]$OutLog, [string]$ErrorLog) {
    # Windows uvicorn reload broadcasts CTRL_C_EVENT in its console. Keeping
    # Python in PowerShell's pipeline also cancels this launcher on reload.
    # Give the reloader its own hidden console and write logs directly to disk.
    $child = Start-Process -FilePath $Python -ArgumentList $Arguments -WorkingDirectory $ProjectRoot -WindowStyle Hidden -RedirectStandardOutput $OutLog -RedirectStandardError $ErrorLog -Wait -PassThru -ErrorAction Stop
    return $child.ExitCode
}

function Wait-RepositoryBackend([string]$ProjectRoot, [int]$BackendPort, [int]$Seconds, [string]$LogFile, [int]$LauncherId = 0) {
    $timer = [Diagnostics.Stopwatch]::StartNew()
    $lastErr = '尚未发现本仓库监听进程'
    while ($timer.Elapsed.TotalSeconds -lt $Seconds) {
        try {
            $inventory = Get-BackendInventory $ProjectRoot $BackendPort
            if ($inventory.Foreign.Count) {
                $ids = ($inventory.Foreign.OwningProcess | Sort-Object -Unique) -join ','
                Write-Host "[失败] 端口 $BackendPort 的 PID $ids 无法核验为本仓库进程，未进行启停。"
                return $false
            }
            if ($inventory.Listeners.Count -and (Test-BackendHealth $BackendPort)) {
                # HTTP 完成后再次核验监听 PID，防止把请求期间换掉的服务当成功。
                $confirmed = Get-BackendInventory $ProjectRoot $BackendPort
                $before = @($inventory.Listeners.OwningProcess | Sort-Object -Unique) -join ','
                $after = @($confirmed.Listeners.OwningProcess | Sort-Object -Unique) -join ','
                if (-not $confirmed.Foreign.Count -and $before -eq $after) {
                    Write-Host "[backend] 本仓库后端已就绪：http://127.0.0.1:$BackendPort"
                    return $true
                }
                $lastErr = '健康检查期间监听进程已变化'
            }
        } catch { $lastErr = $_.Exception.Message }
        if ($LauncherId -and -not (Get-Process -Id $LauncherId -ErrorAction SilentlyContinue)) {
            $lastErr = '本次启动进程已退出'
            break
        }
        Start-Sleep -Milliseconds 400
    }
    Write-Host "[失败] 后端在 $Seconds 秒内未就绪：$lastErr"
    if ($LogFile -and (Test-Path -LiteralPath $LogFile)) {
        Get-Content -LiteralPath $LogFile -Encoding UTF8 -Tail 40 | ForEach-Object { Write-Host "  $_" }
    }
    return $false
}

try {
    if ($Web) { throw '生产网页托管已下线：8710 只提供 API，不再托管 web/dist。开发调试请用 start.bat dev，打包分发请用桌面端。' }
    if ($Stop -and ($Restart -or $Ensure -or $WaitOnly)) { throw '-Stop 不能与其他操作同时使用。' }
    if ($WaitOnly -and ($Restart -or $Ensure)) { throw '-WaitOnly 不能启动服务。' }
    if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
    $Root = [IO.Path]::GetFullPath($Root)
    if (-not (Test-Path -LiteralPath (Join-Path $Root 'backend\main.py'))) { throw 'Root 不是本项目源码目录。' }
    $venvPy = Join-Path $Root 'backend/.venv/Scripts\python.exe'
    $logDir = Join-Path $Root 'output\runtime'
    if (-not $Log) { $Log = Join-Path $logDir 'backend.log' }

    if ($WaitOnly) {
        if (Wait-RepositoryBackend $Root $Port $TimeoutSec $Log $LauncherProcessId) { exit 0 }
        exit 1
    }
    $inventory = Get-BackendInventory $Root $Port
    if ($inventory.Foreign.Count) {
        $ids = ($inventory.Foreign.OwningProcess | Sort-Object -Unique) -join ','
        throw "端口 $Port 被无法核验为本仓库的 PID $ids 占用；未结束任何进程。"
    }
    if ($Stop -or $Restart) {
        Stop-RepositoryBackend $inventory
        if ($Stop) { Write-Host '[stop] 本仓库后端已停止，或原本未运行。'; exit 0 }
        # 等待旧监听释放；不能把旧服务的健康响应误认为重启成功。
        $release = [Diagnostics.Stopwatch]::StartNew()
        do {
            $inventory = Get-BackendInventory $Root $Port
            if (-not $inventory.Owned.Count -and -not $inventory.Listeners.Count) { break }
            if ($inventory.Foreign.Count -or $release.Elapsed.TotalSeconds -ge 5) { throw '旧端口未释放，重启已中止。' }
            Start-Sleep -Milliseconds 100
        } while ($true)
    } elseif ($inventory.Owned.Count -or $inventory.Listeners.Count) {
        if (Wait-RepositoryBackend $Root $Port $TimeoutSec $Log) {
            Write-Host '[backend] 复用已有健康服务；本次未重启。'
            exit 0
        }
        throw '已有后端未就绪；请检查日志，需要重启时显式执行 start.bat restart。'
    }
    if (-not (Test-Path -LiteralPath $venvPy)) { throw '找不到虚拟环境，请运行 tools\env_doctor.ps1。' }

    if ($Ensure -or $Restart) {
        $psExe = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $launchArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $PSCommandPath),
                        '-Root', ('"{0}"' -f $Root), '-Port', [string]$Port, '-Log', ('"{0}"' -f $Log))
        if ($Reload) { $launchArgs += '-Reload' }
        # Detach hidden service output from the short-lived initiating console.
        # Keep native/reloader errors even after the caller has exited.
        New-Item -ItemType Directory -Force $logDir -ErrorAction Stop | Out-Null
        $launchStamp = [Guid]::NewGuid().ToString('N')
        $launchOut = Join-Path $logDir "backend-launch-$Port-$launchStamp.out.log"
        $launchError = Join-Path $logDir "backend-launch-$Port-$launchStamp.error.log"
        $launcher = Start-Process -FilePath $psExe -ArgumentList $launchArgs -WorkingDirectory $Root -WindowStyle Hidden -RedirectStandardOutput $launchOut -RedirectStandardError $launchError -PassThru -ErrorAction Stop
        if (Wait-RepositoryBackend $Root $Port $TimeoutSec $Log $launcher.Id) { exit 0 }
        throw '启动失败，现有进程未被自动清理；请查看日志。'
    }

    New-Item -ItemType Directory -Force $logDir -ErrorAction Stop | Out-Null
    if (Test-Path -LiteralPath $Log) {
        Move-Item -LiteralPath $Log -Destination (Join-Path (Split-Path -Parent $Log) 'backend.prev.log') -Force -ErrorAction Stop
    }
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUNBUFFERED = '1'
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    Set-Location -LiteralPath $Root
    Write-Host "[backend] http://127.0.0.1:$Port   log: $Log"
    # 不做 import main 的预检：那会提前创建 World 并写入实际项目数据。
    $uv = @('-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', [string]$Port)
    # Acceptance tools and output artifacts must never restart a live link.
    if ($Reload) {
        # Camera streams/WebSockets can outlive a reload. Bound only the dev
        # server's graceful drain so its old worker cannot hold the API forever.
        $uv += @('--reload', '--reload-dir', (Join-Path $Root 'backend'), '--timeout-graceful-shutdown', '5')
    }
    $errorLog = [IO.Path]::ChangeExtension($Log, '.error.log')
    $backendCode = Invoke-BackendProcess $venvPy $uv $Root $Log $errorLog
    Write-Host "[backend] 已退出，退出码 $backendCode；日志：$Log"
    exit $backendCode
} catch {
    Write-Host "[失败] $($_.Exception.Message)"
    exit 1
}
