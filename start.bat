@echo off
rem ============================================================
rem  FreeRemote 一键启动：双击即可
rem    - 隐藏后台启动（看门狗托管：崩溃自动重启、改动热更新）
rem    - 本窗口可以随时关掉，不影响服务
rem    - 启动完成自动打开浏览器并显示手机访问地址
rem  停止：双击 stop.bat
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"
setlocal enabledelayedexpansion

rem ---------- [1/3] 环境准备（首次运行自动装依赖） ----------
if not exist ".venv\Scripts\python.exe" (
    echo [1/3] 首次运行：创建虚拟环境并安装依赖（可能需要几分钟）...
    py -3 -m venv .venv
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
    type nul > ".venv\.deps_ok"
)
if not exist ".venv\.deps_ok" (
    echo [1/3] 安装依赖...
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
    type nul > ".venv\.deps_ok"
)

rem ---------- 解析 --port（默认 8080） ----------
set PORT=8080
set PORTNEXT=
for %%a in (%*) do (
    if "%%a"=="--port" set PORTNEXT=1
    if defined PORTNEXT if not "%%a"=="--port" (
        set PORT=%%a
        set PORTNEXT=
    )
)

rem ---------- 已在运行？直接展示地址退出 ----------
netstat -ano | findstr /C:":%PORT% " | findstr /C:"LISTENING" >nul 2>&1
if not errorlevel 1 (
    echo FreeRemote 已在运行，无需重复启动。
    goto :show_url
)

rem ---------- 启动参数：无参数且存在中继配置 -> 自动连中继（与 run.bat 一致） ----------
set "RUN_ARGS=%*"
if not defined RUN_ARGS if exist "relay-config.txt" (
    set /p RUN_ARGS=<relay-config.txt
    echo 检测到中继配置，自动连接: !RUN_ARGS!
)

rem ---------- [2/3] 隐藏后台启动看门狗 ----------
rem pid 文件由看门狗自己写入（单实例锁：重复启动会被拒绝并记录到 logs/watchdog.log）
echo [2/3] 后台启动 FreeRemote（看门狗托管，关掉本窗口服务照常运行）...
if not exist ".freebuff" mkdir ".freebuff"
powershell -NoProfile -Command "Start-Process -FilePath '%CD%\.venv\Scripts\python.exe' -ArgumentList 'hot.py %RUN_ARGS%' -WorkingDirectory '%CD%' -WindowStyle Hidden" >nul 2>&1

rem ---------- [3/3] 健康检查：最多等 30 秒 ----------
echo [3/3] 等待服务就绪...
set /a TRIES=0
:waitloop
rem ping 计时 ~2 秒（timeout 在部分环境被 GNU 工具遮蔽）
ping -n 3 127.0.0.1 >nul
set /a TRIES+=1
curl -s -m 3 -o nul "http://127.0.0.1:%PORT%/" >nul 2>&1
if not errorlevel 1 goto :up
rem curl 不可用时的 TCP 兜底探测
powershell -NoProfile -Command "if((New-Object Net.Sockets.TcpClient('127.0.0.1',%PORT%)).Connected){exit 0}else{exit 1}" >nul 2>&1
if not errorlevel 1 goto :up
if %TRIES% GEQ 15 (
    echo [错误] 服务 30 秒内未就绪，已停止本次启动（进程已清理）。
    echo 排障顺序：先看 logs\watchdog.log（看门狗行为），再看 logs\server.log（服务日志）
    echo 常见原因：端口被占用（双击 stop.bat 清理后重试）/ 依赖未装完
    rem 失败时清掉 pid 记录并停掉半启动的看门狗，避免下次被单实例锁拒绝
    if exist ".freebuff\hot.pid" for /f %%p in (.freebuff\hot.pid) do tasklist /FI "PID eq %%p" 2>nul | findstr /I "python" >nul 2>&1 && taskkill /F /PID %%p >nul 2>&1
    del ".freebuff\hot.pid" >nul 2>&1
    powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'hot\.py|server\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    pause
    exit /b 1
)
goto :waitloop

:up
echo 服务已就绪。
set /p HPID=<".freebuff\hot.pid" 2>nul
if defined HPID echo （看门狗 PID !HPID! 托管运行中）
set /p FTOKEN=<"token.txt" 2>nul
if not defined FTOKEN set "FTOKEN="
start "" "http://127.0.0.1:%PORT%/?token=%FTOKEN%"

:show_url
rem ---------- 展示手机访问地址（复用项目自身的地址探测） ----------
if not defined FTOKEN 2>nul set /p FTOKEN=<"token.txt"
echo.
".venv\Scripts\python.exe" -c "import os,sys; sys.path.insert(0,'.'); from free_remote.netinfo import lan_ips, tailscale_ips; tok=open('token.txt').readline().strip() if os.path.exists('token.txt') else ''; p,ips=lan_ips(); ts=tailscale_ips(); print('   ──────────── 手机访问地址 ────────────'); [print('   局域网 : http://'+x+':'+str(sys.argv[1])+'/?token='+tok) for x in ips if not x.startswith('127.')]; [print('   跨网络(Tailscale): http://'+x+':'+str(sys.argv[1])+'/?token='+tok) for x in ts]; print('   手机与电脑同一 WiFi 时，逐个试上面的局域网地址'); print('   停止：双击 stop.bat（后台服务也会停止）')" %PORT%
echo.
echo （按任意键关闭本窗口，后台服务不受影响）
pause >nul
exit /b 0
