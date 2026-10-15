@echo off
rem 启动华沿手动控制面板。默认连真机 192.168.0.10；run_panel.bat mock 则连本机仿真控制器。
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [错误] 找不到 %PY%，请先创建项目虚拟环境。
  pause
  exit /b 2
)

if "%1"=="mock" (
  start "huayan-mock" /min "%PY%" tools\mock_server.py
  powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; 1..20 | %% { try { $c=New-Object Net.Sockets.TcpClient; $c.Connect('127.0.0.1',10003); $c.Close(); $ok=$true;break } catch {}; Start-Sleep -Milliseconds 250 }; if(-not $ok){exit 1}"
  if errorlevel 1 echo [警告] mock 控制器端口尚未确认，面板仍会启动。
  "%PY%" app.py --mock
  taskkill /FI "WINDOWTITLE eq huayan-mock" /T >nul 2>&1
  goto :end
)

if "%1"=="" (
  "%PY%" app.py
) else (
  "%PY%" app.py --host "%~1"
)

:end
rem 窗口自己关掉，避免启动失败时一闪而过看不到报错
echo.
echo 面板已退出。如有报错请截图。
pause
