@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONIOENCODING=utf-8"
set "PS_LAUNCHER=powershell.exe"
where pwsh.exe >nul 2>nul
if not errorlevel 1 set "PS_LAUNCHER=pwsh.exe"
set "ACTION=%~1"
if "%ACTION%"=="" set "ACTION=start"
if /i "%ACTION%"=="stop" goto stop
if /i "%ACTION%"=="start" goto desktop
if /i "%ACTION%"=="dev" goto prepare
if /i "%ACTION%"=="build" goto prepare
if /i "%ACTION%"=="restart" goto prepare
if /i "%ACTION%"=="package" goto package
echo Usage: start.bat [start^|dev^|build^|restart^|stop]
exit /b 2

:prepare
%PS_LAUNCHER% -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\env_doctor.ps1"
if errorlevel 1 exit /b %errorlevel%
set "FRONTEND_ARGS="
if /i "%ACTION%"=="dev" set "FRONTEND_ARGS=-Dev"
if /i "%ACTION%"=="build" set "FRONTEND_ARGS=-ForceBuild"
%PS_LAUNCHER% -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\check_frontend.ps1" %FRONTEND_ARGS%
if errorlevel 1 exit /b %errorlevel%
if /i "%ACTION%"=="build" exit /b 0
set "BACKEND_ARGS=-Ensure"
if /i "%ACTION%"=="dev" set "BACKEND_ARGS=-Ensure -Reload"
if /i "%ACTION%"=="restart" set "BACKEND_ARGS=-Ensure -Restart"
%PS_LAUNCHER% -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\run_backend.ps1" %BACKEND_ARGS%
if errorlevel 1 exit /b %errorlevel%
if /i "%ACTION%"=="dev" goto dev
goto desktop
:dev
pushd "%~dp0web"
call npm.cmd run dev
set "RESULT=%errorlevel%"
popd
exit /b %RESULT%

:stop
if exist "%~dp0output\desktop-release\win-unpacked\JiuShuRobotStudio.exe" (
  start "" "%~dp0output\desktop-release\win-unpacked\JiuShuRobotStudio.exe" --quit
  exit /b 0
)
if exist "%~dp0node_modules\electron\dist\electron.exe" (
  start "" "%~dp0node_modules\electron\dist\electron.exe" "%~dp0." --quit
  exit /b 0
)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\run_backend.ps1" -Stop
exit /b %errorlevel%

:desktop
if exist "%~dp0output\desktop-release\win-unpacked\JiuShuRobotStudio.exe" (
  start "" "%~dp0output\desktop-release\win-unpacked\JiuShuRobotStudio.exe"
  exit /b 0
)
if not exist "%~dp0node_modules\electron\dist\electron.exe" (
  echo Desktop runtime missing. Run npm install, then start.bat.
  exit /b 3
)
if not exist "%~dp0web\dist\index.html" (
  echo Frontend build missing. Run start.bat build first.
  exit /b 5
)
start "" "%~dp0node_modules\electron\dist\electron.exe" "%~dp0."
exit /b 0

:package
where pwsh.exe >nul 2>nul
if errorlevel 1 (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\build_desktop.ps1" -Installer
) else (
  pwsh.exe -NoProfile -File "%~dp0tools\build_desktop.ps1" -Installer
)
exit /b %errorlevel%
