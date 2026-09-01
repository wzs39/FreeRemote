@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 尚未安装依赖，先运行 run.bat 完成首次安装。
    pause
    exit /b 1
)
".venv\Scripts\python.exe" doctor.py %*
pause
