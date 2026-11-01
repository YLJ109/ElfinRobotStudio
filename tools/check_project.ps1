[CmdletBinding()]
param([switch]$SkipBuild)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$projectPython = Join-Path $projectRoot 'backend/.venv/Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) { throw 'Project virtual environment is missing.' }
$env:PYTHONIOENCODING = 'utf-8'
Push-Location -LiteralPath $projectRoot
try {
    & $projectPython -B tools/check_project_layout.py
    if ($LASTEXITCODE -ne 0) { throw 'Project layout verification failed.' }
    Push-Location -LiteralPath (Join-Path $projectRoot 'web')
    try {
        & npm.cmd test
        if ($LASTEXITCODE -ne 0) { throw 'Frontend regression failed.' }
        if (-not $SkipBuild) {
            & npm.cmd run build
            if ($LASTEXITCODE -ne 0) { throw 'Frontend production build failed.' }
        }
    } finally { Pop-Location }
    # All stateful checks run only in the disposable, hardware-disabled copy.
    $projectChecks = (Get-Content -LiteralPath 'tools/offline_checks.json' -Raw | ConvertFrom-Json).checks
    & $projectPython -B tools/run_offline_checks.py @projectChecks
    if ($LASTEXITCODE -ne 0) { throw 'Isolated backend regression failed.' }
    Write-Host 'PROJECT_CHECKS_OK'
} catch {
    Write-Error $_
    exit 1
} finally { Pop-Location }
