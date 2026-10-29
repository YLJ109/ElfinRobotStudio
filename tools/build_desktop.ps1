[CmdletBinding()]
param([switch]$Installer)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
& ./tools/check_frontend.ps1
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& ./.venv/Scripts/python.exe tools/build_desktop_icon.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& ./.venv/Scripts/python.exe -m PyInstaller --noconfirm --distpath output/desktop-engine --workpath output/desktop-build desktop/backend.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if ($Installer) { & npm.cmd run dist } else { & npm.cmd run pack }
exit $LASTEXITCODE
