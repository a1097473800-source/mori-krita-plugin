@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-installed.ps1"
set "moriExitCode=%errorlevel%"
if not "%moriExitCode%"=="0" echo 更新未完成，请查看上方提示。
pause
exit /b %moriExitCode%
