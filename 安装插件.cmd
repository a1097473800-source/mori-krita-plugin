@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"
set "moriExitCode=%errorlevel%"
if not "%moriExitCode%"=="0" echo 安装未完成，请查看上方提示。
pause
exit /b %moriExitCode%

