@echo off
cd /d "%~dp0"
echo ============================================================
echo  FreeRemote — 重新生成访问口令（默认 7 天有效）
echo  用法：双击本文件 = 7 天；带数字 = 指定天数
echo      （如：renew-token.bat 30  =  30 天；renew-token.bat 0 = 永久）
echo ============================================================
echo.
set DAYS=%~1
if "%DAYS%"=="" set DAYS=7
".venv\Scripts\python.exe" server.py --renew-token %DAYS%
echo.
echo 手机保存上面「手机访问(Tailscale跨网)」那一行的地址即可。
echo 口令到期后手机将无法连接，届时重新双击本文件换新口令即可。
echo.
pause