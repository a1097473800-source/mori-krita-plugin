@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0update-installed.ps1" -CheckOnly
set "moriExitCode=%errorlevel%"
pause
exit /b %moriExitCode%
