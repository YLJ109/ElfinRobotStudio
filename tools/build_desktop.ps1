[CmdletBinding()]
param([switch]$Installer)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
& ./tools/check_frontend.ps1 -ForceBuild
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& ./backend/.venv/Scripts/python.exe tools/build_desktop_icon.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& ./backend/.venv/Scripts/python.exe -B tools/write_release_identity.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if (-not (Test-Path -LiteralPath 'backend/.venv-vision/Scripts/python.exe')) { throw 'Missing isolated vision build environment; install backend/requirements-vision-lock.txt there first.' }
& ./backend/.venv-vision/Scripts/python.exe -m PyInstaller --noconfirm --distpath output/desktop-vision --workpath output/desktop-vision-build desktop/vision_worker.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& ./backend/.venv/Scripts/python.exe -m PyInstaller --noconfirm --distpath output/desktop-engine --workpath output/desktop-build desktop/backend.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
if ($Installer) { & npm.cmd run dist } else { & npm.cmd run pack }
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
& ./backend/.venv/Scripts/python.exe -B tools/write_release_identity.py --verify
exit $LASTEXITCODE
