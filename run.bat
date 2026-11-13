@echo off
rem gyeoljae launcher (Windows). e.g.  run.bat -u kim list   /   run.bat serve --port 8130
chcp 65001 >nul
set "PYTHONPATH=%~dp0src;%PYTHONPATH%"
set PYTHONIOENCODING=utf-8
where py >nul 2>nul
if %ERRORLEVEL%==0 (
  py -3 -m gyeoljae %*
) else (
  python -m gyeoljae %*
)
exit /b %ERRORLEVEL%
