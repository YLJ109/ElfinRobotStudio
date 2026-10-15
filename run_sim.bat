@echo off
rem 启动华沿 Elfin 点位仿真与监控系统（后端 + 浏览器）
rem   run_sim.bat            打开 http://127.0.0.1:8710
rem   run_sim.bat dev        前后端分离热更新（后端 8710 + vite 5173）
setlocal
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
  echo [错误] 找不到 %PY%，请先创建项目虚拟环境。
  pause
  exit /b 2
)

if "%1"=="stop" goto stop

if "%1"=="dev" goto dev

rem 先结束本项目旧后端，避免端口占用导致浏览器继续连接旧版本。
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | ? { $_.CommandLine -match 'sim.backend.main:app.*--port 8710' } | %% { Stop-Process -Id $_.ProcessId -Force }"
echo 正在构建前端资源...
pushd sim\web
call npm run build
if errorlevel 1 (
  popd
  echo [错误] 前端构建失败，未启动旧版本。
  pause
  exit /b 1
)
popd
start "huayan-sim-backend" /min "%PY%" -m uvicorn sim.backend.main:app --host 127.0.0.1 --port 8710
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ok=$false; 1..30 | %% { try { $s=Invoke-WebRequest http://127.0.0.1:8710/api/state -UseBasicParsing -TimeoutSec 1; $t=Invoke-WebRequest http://127.0.0.1:8710/api/tools -UseBasicParsing -TimeoutSec 1; if($s.StatusCode -eq 200 -and $t.StatusCode -eq 200){$ok=$true;break} } catch {}; Start-Sleep -Milliseconds 300 }; if(-not $ok){exit 1}"
if errorlevel 1 (
  echo [错误] 后端未能在 9 秒内启动，请检查 8710 端口或后端窗口。
  pause
  exit /b 1
)
start "" http://127.0.0.1:8710/
echo 已启动：http://127.0.0.1:8710
echo 后端在独立窗口运行；停止请执行 run_sim.bat stop。
pause
goto end

:dev
start "huayan-sim-backend" "%PY%" -m uvicorn sim.backend.main:app --host 127.0.0.1 --port 8710 --reload
pushd sim\web
call npm run dev
popd
goto end

:stop
echo 正在停止本项目启动的后端进程...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | ? { $_.CommandLine -match 'sim.backend.main:app.*--port 8710' } | %% { Stop-Process -Id $_.ProcessId -Force }"
echo 已发送停止请求。
goto end

:end
endlocal
