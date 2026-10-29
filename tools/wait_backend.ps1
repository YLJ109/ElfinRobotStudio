<#
等待本仓库后端就绪，只检查状态，不启动、停止或重启服务。
复用 run_backend.ps1 的仓库归属检查及状态 JSON 检查，避免两套健康口径。
UTF-8 BOM 兼容 Windows PowerShell 5.1；退出码 0 就绪 / 1 未就绪。
#>
[CmdletBinding()]
param(
    [ValidateRange(1, 65535)][int]$Port = 8710,
    [string]$Log = '',
    [ValidateRange(1, 600)][int]$TimeoutSec = 30,
    [string]$Root = '',
    [int]$LauncherProcessId = 0
)

if (-not $Root) { $Root = Split-Path -Parent $PSScriptRoot }
& (Join-Path $PSScriptRoot 'run_backend.ps1') -Root $Root -Port $Port -Log $Log -WaitOnly -TimeoutSec $TimeoutSec -LauncherProcessId $LauncherProcessId
exit $LASTEXITCODE
