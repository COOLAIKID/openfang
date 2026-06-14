@echo off
REM ─────────────────────────────────────────────────────────────────────────────
REM  AutoEarn — Windows Startup Script
REM  Double-click or run from command prompt:
REM    autoearn.bat           - launch desktop GUI
REM    autoearn.bat --no-gui  - headless (dashboard + agents)
REM    autoearn.bat --cli     - console mode (no Qt)
REM    autoearn.bat --stop    - stop running instance
REM ─────────────────────────────────────────────────────────────────────────────
setlocal enabledelayedexpansion

set SCRIPT_DIR=%~dp0
set VENV_DIR=%SCRIPT_DIR%.venv
set LOG_DIR=%SCRIPT_DIR%logs
set PID_FILE=%LOG_DIR%\autoearn.pid
set LOG_FILE=%LOG_DIR%\autoearn.log

REM Parse arguments
set MODE=gui
set EXTRA_ARGS=
set ACTION=start

:arg_loop
if "%~1"=="" goto arg_done
if "%~1"=="--no-gui"    ( set MODE=no-gui & set EXTRA_ARGS=!EXTRA_ARGS! --no-gui & shift & goto arg_loop )
if "%~1"=="--cli"       ( set MODE=cli    & set EXTRA_ARGS=!EXTRA_ARGS! --cli    & shift & goto arg_loop )
if "%~1"=="--debug"     ( set EXTRA_ARGS=!EXTRA_ARGS! --debug     & shift & goto arg_loop )
if "%~1"=="--no-tray"   ( set EXTRA_ARGS=!EXTRA_ARGS! --no-tray   & shift & goto arg_loop )
if "%~1"=="--stop"      ( set ACTION=stop   & shift & goto arg_loop )
if "%~1"=="--status"    ( set ACTION=status & shift & goto arg_loop )
if "%~1"=="--help"      goto show_help
if "%~1"=="/?"          goto show_help
shift
goto arg_loop
:arg_done

if "%ACTION%"=="stop" goto do_stop
if "%ACTION%"=="status" goto do_status

REM ── Banner ──────────────────────────────────────────────────────────────────
echo.
echo  $$  AutoEarn v1.0  $$  Autonomous AI Organization
echo.

REM ── Python check ─────────────────────────────────────────────────────────────
where python >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Python not found. Install from https://www.python.org/downloads/
    pause
    exit /b 1
)

for /f "delims=" %%V in ('python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"') do set PY_VER=%%V
echo [AutoEarn] Using Python %PY_VER%

REM ── Virtual environment ───────────────────────────────────────────────────────
if not exist "%VENV_DIR%\" (
    echo [AutoEarn] Creating virtual environment...
    python -m venv "%VENV_DIR%"
)

call "%VENV_DIR%\Scripts\activate.bat"

REM ── Install dependencies ──────────────────────────────────────────────────────
if not exist "%VENV_DIR%\.deps_installed" (
    echo [AutoEarn] Installing dependencies...
    python -m pip install --upgrade pip --quiet

    if exist "%SCRIPT_DIR%autoearn\requirements.txt" (
        python -m pip install -r "%SCRIPT_DIR%autoearn\requirements.txt" --quiet
    ) else if exist "%SCRIPT_DIR%requirements.txt" (
        python -m pip install -r "%SCRIPT_DIR%requirements.txt" --quiet
    )

    if not "%MODE%"=="cli" (
        echo [AutoEarn] Installing PyQt6 for desktop UI...
        python -m pip install PyQt6 --quiet 2>nul
    )

    echo. > "%VENV_DIR%\.deps_installed"
    echo [AutoEarn] Dependencies installed.
)

REM ── Config check ─────────────────────────────────────────────────────────────
if not exist "%SCRIPT_DIR%autoearn\config.toml" (
    echo [AutoEarn] Creating minimal config.toml...
    (
        echo [providers.groq]
        echo api_key = ""  # Add your Groq API key here
        echo.
        echo [providers.gemini]
        echo api_key = ""
        echo.
        echo [providers.ollama]
        echo enabled = true
        echo model = "llama3"
        echo.
        echo [dashboard]
        echo port = 4200
        echo host = "127.0.0.1"
        echo.
        echo [database]
        echo path = "autoearn.db"
        echo.
        echo [ui]
        echo theme = "dark"
    ) > "%SCRIPT_DIR%autoearn\config.toml"
    echo [AutoEarn] Edit autoearn\config.toml to add your API keys.
)

REM ── Seed agents (first run) ───────────────────────────────────────────────────
if not exist "%SCRIPT_DIR%.seed_done" (
    echo [AutoEarn] Seeding agents...
    cd /d "%SCRIPT_DIR%"
    python autoearn\seed_agents.py 2>nul
    echo. > "%SCRIPT_DIR%.seed_done"
)

REM ── Create log directory ──────────────────────────────────────────────────────
if not exist "%LOG_DIR%\" mkdir "%LOG_DIR%"

REM ── Launch ───────────────────────────────────────────────────────────────────
cd /d "%SCRIPT_DIR%"
echo [AutoEarn] Starting in %MODE% mode...

if "%MODE%"=="no-gui" (
    start /b python -m autoearn.desktop.app %EXTRA_ARGS% >> "%LOG_FILE%" 2>&1
    echo [AutoEarn] AutoEarn started in background.
    echo [AutoEarn] Dashboard: http://localhost:4200
    echo [AutoEarn] Logs: %LOG_FILE%
    echo [AutoEarn] Stop with: autoearn.bat --stop
) else (
    python -m autoearn.desktop.app %EXTRA_ARGS%
)

goto end

:do_stop
taskkill /f /im python.exe /fi "WINDOWTITLE eq AutoEarn*" >nul 2>&1
echo [AutoEarn] Stop signal sent.
goto end

:do_status
echo [AutoEarn] Checking status...
tasklist | findstr /i python >nul 2>&1 && (
    echo [AutoEarn] Python process(es) running. Dashboard: http://localhost:4200
) || (
    echo [AutoEarn] No AutoEarn process found.
)
goto end

:show_help
echo Usage: autoearn.bat [--no-gui] [--cli] [--debug] [--stop] [--status]
echo.
echo   --no-gui   Run headless (dashboard + agents, no desktop window^)
echo   --cli      Console mode (no Qt required^)
echo   --debug    Enable debug logging
echo   --stop     Stop running AutoEarn instance
echo   --status   Show running status
goto end

:end
endlocal
