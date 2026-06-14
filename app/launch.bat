@echo off
REM AutoEarn launcher (Windows). Double-clicking the shortcut runs this:
REM start the server if needed, then open the dashboard in the browser.
setlocal
set "ROOT=%~dp0.."
set "VENV=%ROOT%\.venv"
set "PORT=4200"
set "URL=http://localhost:%PORT%"

REM Already running? Just open it.
powershell -NoProfile -Command "try{ if((Invoke-WebRequest -UseBasicParsing '%URL%/api/health' -TimeoutSec 2).StatusCode -eq 200){exit 0} }catch{}; exit 1" >nul 2>&1
if %errorlevel%==0 ( start "" "%URL%" & exit /b 0 )

REM First run: create venv + install slim deps.
if not exist "%VENV%\Scripts\python.exe" (
  where py >nul 2>&1 && ( py -m venv "%VENV%" ) || ( python -m venv "%VENV%" )
  "%VENV%\Scripts\python.exe" -m pip install --quiet --upgrade pip
  "%VENV%\Scripts\python.exe" -m pip install --quiet -r "%ROOT%\autoearn\requirements-cloud.txt"
)

REM Start the server (from autoearn\ for imports), hidden.
cd /d "%ROOT%\autoearn"
set "HOST=127.0.0.1"
start "" /b "%VENV%\Scripts\python.exe" main.py

REM Wait for it to come up, then open the browser.
powershell -NoProfile -Command "for($i=0;$i -lt 60;$i++){ try{ if((Invoke-WebRequest -UseBasicParsing '%URL%/api/health' -TimeoutSec 2).StatusCode -eq 200){ Start-Process '%URL%'; exit 0 } }catch{}; Start-Sleep -Milliseconds 500 }; exit 1"
exit /b %errorlevel%
