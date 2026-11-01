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
if /i "%ACTION%"=="start" goto hotdev
if /i "%ACTION%"=="web" goto webretired
if /i "%ACTION%"=="dev" goto hotdev
if /i "%ACTION%"=="build" goto prepare
if /i "%ACTION%"=="restart" goto hotdev
if /i "%ACTION%"=="package" goto package
echo Usage: start.bat [start^|dev^|build^|restart^|stop]
exit /b 2

:webretired
echo [提示] 生产网页托管已下线：8710 只提供 API，不再托管 web/dist 页面。
echo        开发调试请执行 start.bat dev（Vite 热更新 + 后端）；
echo        对外分发请执行 start.bat package 打包后运行桌面端。
exit /b 1

:prepare
%PS_LAUNCHER% -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\env_doctor.ps1"
if errorlevel 1 exit /b %errorlevel%
%PS_LAUNCHER% -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\check_frontend.ps1" -ForceBuild
exit /b %errorlevel%

:hotdev
%PS_LAUNCHER% -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\run_dev.ps1" -Open
exit /b %errorlevel%

:stop
%PS_LAUNCHER% -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\run_dev.ps1" -Stop
exit /b %errorlevel%

:package
where pwsh.exe >nul 2>nul
if errorlevel 1 (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\build_desktop.ps1" -Installer
) else (
  pwsh.exe -NoProfile -File "%~dp0tools\build_desktop.ps1" -Installer
)
exit /b %errorlevel%
