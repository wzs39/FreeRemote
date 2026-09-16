@echo off
rem ============================================================
rem  FreeRemote 一键停止
rem    - 先杀看门狗（防止服务被自动重启"复活"）
rem    - 再清扫全部服务进程（兼容旧实例 / pid 文件丢失的情况）
rem  重启：双击 start.bat
rem ============================================================
chcp 65001 >nul
cd /d "%~dp0"
setlocal enabledelayedexpansion
echo 正在停止 FreeRemote...

rem 1) start.bat 记录的看门狗 pid（确认是 python 进程才杀，防 PID 复用误杀）
if exist ".freebuff\hot.pid" (
    set /p HPID=<".freebuff\hot.pid"
    if defined HPID (
        tasklist /FI "PID eq !HPID!" 2>nul | findstr /I "python" >nul 2>&1 && (
            taskkill /F /PID !HPID! >nul 2>&1 && echo    已停止看门狗 PID !HPID!
        )
    )
)
del ".freebuff\hot.pid" >nul 2>&1

rem 2) 清扫全部看门狗与服务进程（hot.py / server.py / relay.py）
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'python' -and $_.CommandLine -match 'hot\.py|server\.py|relay\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host ('   已停止 PID ' + $_.ProcessId) }"

echo 完成。双击 start.bat 可重新启动。
pause
