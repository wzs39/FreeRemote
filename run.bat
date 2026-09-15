@echo off
cd /d "%~dp0"
setlocal enabledelayedexpansion

rem 防重复启动：端口已被占用时提示（中继模式不监听端口，跳过检查）
rem 提取 --port 参数（默认 8080）
set CHK_PORT=8080
for %%a in (%*) do (
    if "%%a"=="--port" set PORT_NEXT=1
    if defined PORT_NEXT if not "%%a"=="--port" (
        set CHK_PORT=%%a
        set PORT_NEXT=
    )
)
echo %* | findstr /i /C:--relay >nul 2>&1
if errorlevel 1 (
    netstat -ano | findstr /C:":%CHK_PORT% " | findstr /C:"LISTENING" >nul 2>&1
    if not errorlevel 1 (
        echo [提示] %CHK_PORT% 端口已被占用（可能 FreeRemote 已在运行，或其它程序占用）。
        echo        如需重启，请先运行 stop.bat 清理旧进程，再重新运行 run.bat。
        echo        或用 run.bat --port 其他端口 换一个端口启动。
        pause
        exit /b 1
    )
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/2] 首次运行，创建虚拟环境并安装依赖...
    py -3 -m venv .venv
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
    type nul > ".venv\.deps_ok"
)
if not exist ".venv\.deps_ok" (
    echo [1/2] 安装依赖...
    ".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
    type nul > ".venv\.deps_ok"
)

rem ==================== 启动模式选择 ====================
set RUN_ARGS=%*

if not "%RUN_ARGS%"=="" goto :have_args

if exist "relay-config.txt" (
    rem 双击 + 已有配置 -> 默认自动连中继；可输 R 重新配置
    echo [提示] 检测到中继配置 relay-config.txt。
    echo        连接失败时可重新配置。
    set /p RECFG=回车=用现有配置连接 / R=重新配置：
    if /i "!RECFG!"=="R" goto :configure_relay
    set /p RUN_ARGS=<relay-config.txt
    echo 自动连接公网中继：!RUN_ARGS!
    goto :run
)

rem 双击 + 无配置 -> 询问是否配置公网中继
echo.
echo 未检测到中继配置（relay-config.txt）。
echo   - 直接回车  = 局域网模式（手机连同一 WiFi）
echo   - 输入 O     = 局域网模式 + 一次性口令（10 分钟时效，用完作废）
echo   - 需控制任务管理器/管理员程序 -> 关闭本窗口，改用 run-admin.bat
set /p CHOICE=选择启动方式（回车=局域网 / O=一次性口令 / Y=配置中继）：
if /i "%CHOICE%"=="Y" goto :configure_relay
if /i "%CHOICE%"=="O" set RUN_ARGS=--once
goto :run
:configure_relay
echo.
echo 请输入中继信息（可用自建 / Render / Fly.io 的穿透地址）：
echo   提示: 填域名即可（如 relay.example.com），自动识别 https/wss，不要带后面的一串路径。
set /p RURL=中继地址 (如 relay.example.com): 
set /p RID=设备识别码 (如 TEST01): 
set /p RPASS=设备口令: 
if "%RURL%"=="" (
    echo [错误] 中继地址不能为空，已取消配置，按局域网模式启动。
    goto :run
)
set RUN_ARGS=--relay %RURL% --id %RID% --password %RPASS%
echo %RUN_ARGS%> relay-config.txt
echo.
echo [提示] 已保存到 relay-config.txt：
echo        %RUN_ARGS%
echo        下次双击 run.bat 将自动连中继。
echo        可运行 install-autostart.bat 设置开机自启。
echo.
goto :run

:have_args
rem 命令行带参数（含中继模式）时记录到 relay-config.txt，供 install-autostart.bat 做开机自启
echo %RUN_ARGS% | findstr /i /C:--relay >nul 2>&1 && (
    echo %RUN_ARGS%> relay-config.txt
    echo [提示] 已记录中继参数到 relay-config.txt，可运行 install-autostart.bat 设置开机自启
)
goto :run

:run
echo [2/2] 启动 FreeRemote...
".venv\Scripts\python.exe" server.py %RUN_ARGS%
pause