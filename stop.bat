@echo off
echo 正在停止所有 FreeRemote 进程（relay.py / server.py）...
for /f "tokens=1" %%p in ('wmic process where "name='python.exe'" get processid ^| findstr /r "[0-9]"') do (
    wmic process where "processid=%%p" get commandline 2>nul | findstr /i "relay.py server.py" >nul 2>&1 && (
        taskkill /F /PID %%p >nul 2>&1 && echo   已停止 PID %%p
    )
)
echo 完成。可用 run.bat 重新启动。
pause
