@echo off
rem e-approval-kr admin console: an elevated command prompt with e-approval-kr on PATH and
rem GYEOLJAE_DATA pointing at the service data folder (only Administrators may write there).
net session >nul 2>&1
if errorlevel 1 (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)
chcp 65001 >nul
set "GYEOLJAE_DATA=%ProgramData%\e-approval-kr"
set "PATH=%~dp0;%PATH%"
cd /d "%GYEOLJAE_DATA%"
title e-approval-kr admin
echo e-approval-kr admin console    data: %GYEOLJAE_DATA%
echo.
echo   e-approval-kr setup                      first administrator (if not done in the browser)
echo   e-approval-kr -u admin menu              text menu (BBS style)
echo   e-approval-kr backup backup.zip          consistent backup while running
echo   e-approval-kr --help                     all commands
echo   net stop e-approval-kr ^& net start e-approval-kr    restart the service
echo   logs: %GYEOLJAE_DATA%\logs
echo.
cmd /k
