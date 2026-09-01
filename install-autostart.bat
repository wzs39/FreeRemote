@echo off
cd /d "%~dp0"
setlocal enabledelayedexpansion

echo ============================================================
echo  FreeRemote 开机自启安装（自动识别模式）
echo    中继模式 : 有 relay-config.txt 时使用
echo    局域网模式: 无 relay-config.txt 时使用（手机经 Tailscale 直连）
echo ============================================================
echo.

if exist "relay-config.txt" (
    set /p RELAY_ARGS=<relay-config.txt
    echo [提示] 检测到中继配置，将注册「中继模式」开机自启。
    set TASK_NAME=FreeRemoteRelay
    set SCRIPT_NAME=autostart-relay.bat
    set MODE_TXT=中继模式
) else (
    set RELAY_ARGS=
    echo [提示] 未检测到中继配置，将注册「局域网/Tailscale 模式」开机自启。
    echo        口令固定使用 token.txt，手机书签长期有效。
    set TASK_NAME=FreeRemoteLAN
    set SCRIPT_NAME=autostart-lan.bat
    set MODE_TXT=局域网模式
)

echo [1/2] 生成自启脚本 !SCRIPT_NAME! ...
(
    echo @echo off
    echo cd /d "%~dp0"
    echo ".venv\Scripts\pythonw.exe" server.py !RELAY_ARGS! ^>^> auto.log 2^>^&1
) > !SCRIPT_NAME!

echo [2/2] 注册 Windows 计划任务（登录时自动运行）...
schtasks /Create /F /TN "!TASK_NAME!" /SC ONLOGON /RL LIMITED /TR "\"%~dp0!SCRIPT_NAME!\"" >nul 2>&1
if errorlevel 1 (
    echo [错误] 计划任务创建失败，请用管理员身份运行本脚本。
    pause
    exit /b 1
)

echo.
echo 完成！下次登录 Windows 将自动以「!MODE_TXT!」启动 FreeRemote。
echo 手机访问地址见启动横幅（含 Tailscale 跨网地址），口令在 token.txt。
echo.
echo 提示：
echo   - Windows 需设置为「自动登录」，否则停在登录界面不会触发
echo   - 卸载自启：schtasks /Delete /TN !TASK_NAME! /F
echo   - 日志：auto.log（同目录）
pause
