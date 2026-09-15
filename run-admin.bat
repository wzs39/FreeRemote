@echo off
rem ============================================================
rem  FreeRemote - administrator mode
rem  Why: Windows silently ignores simulated mouse/keyboard input
rem  when the target window has HIGHER privileges than the sender.
rem  Task Manager (and apps "run as administrator") are elevated,
rem  so a normal FreeRemote process cannot control them.
rem  This script relaunches itself as admin, then starts the
rem  watchdog (hot.py) - crash auto-restart + hot update included.
rem ============================================================
net session >nul 2>&1
if %errorlevel% neq 0 (
  echo Requesting administrator privileges...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
cd /d "%~dp0"
title FreeRemote (Administrator)
echo.
echo  FreeRemote running as ADMINISTRATOR.
echo  Input now reaches elevated windows: Task Manager, admin apps, UAC desktop.
echo  Keep this window open. Stop: close this window or press Ctrl+C.
echo.
.venv\Scripts\python.exe hot.py
pause
