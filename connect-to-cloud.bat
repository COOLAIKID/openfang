@echo off
REM Connect THIS Windows computer to your cloud AutoEarn dashboard.
REM It dials out to your dashboard, so the dashboard can run agents and tasks
REM on this machine. Leave this window open.
setlocal
set "ROOT=%~dp0"
set "PY=%ROOT%.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=python"

if "%AUTOEARN_CLOUD_URL%"=="" set /p AUTOEARN_CLOUD_URL=Your dashboard URL (https://autoearn-xxxx.onrender.com):
if "%AUTOEARN_PASSWORD%"=="" set /p AUTOEARN_PASSWORD=Your dashboard password:
if "%AUTOEARN_MACHINE%"=="" set "AUTOEARN_MACHINE=%COMPUTERNAME%"

echo Connecting %AUTOEARN_MACHINE% to %AUTOEARN_CLOUD_URL% ... (close window to stop)
cd /d "%ROOT%autoearn"
"%PY%" run_runner.py
