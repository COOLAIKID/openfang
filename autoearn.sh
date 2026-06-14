#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  AutoEarn — Linux / macOS Startup Script
#  Usage:
#    ./autoearn.sh            # launch desktop GUI
#    ./autoearn.sh --no-gui   # headless (dashboard + agents only)
#    ./autoearn.sh --cli      # console mode (no Qt required)
#    ./autoearn.sh --update   # update dependencies then launch
#    ./autoearn.sh --stop     # stop all running AutoEarn processes
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
LOG_DIR="${SCRIPT_DIR}/logs"
PID_FILE="${LOG_DIR}/autoearn.pid"
LOG_FILE="${LOG_DIR}/autoearn.log"
PYTHON_MIN_MAJOR=3
PYTHON_MIN_MINOR=10

# ── Colour helpers ──────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

info()    { echo -e "${CYAN}[AutoEarn]${NC} $*"; }
success() { echo -e "${GREEN}[AutoEarn]${NC} $*"; }
warn()    { echo -e "${YELLOW}[AutoEarn]${NC} $*"; }
error()   { echo -e "${RED}[AutoEarn] ERROR:${NC} $*" >&2; }
banner()  { echo -e "\n${BOLD}${BLUE}$*${NC}\n"; }

# ── Parse arguments ──────────────────────────────────────────────────────────
MODE="gui"
EXTRA_ARGS=""

for arg in "$@"; do
    case "$arg" in
        --no-gui)   MODE="no-gui"; EXTRA_ARGS="$EXTRA_ARGS --no-gui" ;;
        --cli)      MODE="cli";    EXTRA_ARGS="$EXTRA_ARGS --cli" ;;
        --debug)    EXTRA_ARGS="$EXTRA_ARGS --debug" ;;
        --no-tray)  EXTRA_ARGS="$EXTRA_ARGS --no-tray" ;;
        --no-splash)EXTRA_ARGS="$EXTRA_ARGS --no-splash" ;;
        --update)   UPDATE=true ;;
        --stop)     ACTION="stop" ;;
        --status)   ACTION="status" ;;
        --help|-h)
            echo "Usage: $0 [--no-gui] [--cli] [--debug] [--update] [--stop] [--status]"
            exit 0
            ;;
    esac
done

ACTION="${ACTION:-start}"

# ── Stop action ──────────────────────────────────────────────────────────────
if [[ "$ACTION" == "stop" ]]; then
    if [[ -f "$PID_FILE" ]]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            info "Stopping AutoEarn (PID $PID)..."
            kill "$PID"
            rm -f "$PID_FILE"
            success "AutoEarn stopped."
        else
            warn "PID $PID not running. Cleaning up stale PID file."
            rm -f "$PID_FILE"
        fi
    else
        # Try pkill as fallback
        pkill -f "autoearn.desktop.app" 2>/dev/null && success "AutoEarn stopped." || warn "No AutoEarn process found."
    fi
    exit 0
fi

# ── Status action ─────────────────────────────────────────────────────────────
if [[ "$ACTION" == "status" ]]; then
    if [[ -f "$PID_FILE" ]]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null; then
            success "AutoEarn is running (PID $PID)"
            echo -e "  Dashboard: ${CYAN}http://localhost:4200${NC}"
        else
            warn "PID file exists but process is dead."
        fi
    else
        warn "AutoEarn is not running."
    fi
    exit 0
fi

# ── Banner ──────────────────────────────────────────────────────────────────
banner "  $$  AutoEarn v1.0  $$  Autonomous AI Organization"

# ── Python check ─────────────────────────────────────────────────────────────
PYTHON=""
for cmd in python3 python python3.12 python3.11 python3.10; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
        MAJOR="${VER%%.*}"
        MINOR="${VER##*.}"
        if (( MAJOR >= PYTHON_MIN_MAJOR && MINOR >= PYTHON_MIN_MINOR )); then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    error "Python ${PYTHON_MIN_MAJOR}.${PYTHON_MIN_MINOR}+ is required but not found."
    error "Install it from https://www.python.org/downloads/"
    exit 1
fi
info "Using Python: $PYTHON ($("$PYTHON" --version))"

# ── Virtual environment ───────────────────────────────────────────────────────
if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating virtual environment at $VENV_DIR..."
    "$PYTHON" -m venv "$VENV_DIR"
    success "Virtual environment created."
fi

# Activate venv
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
PYTHON="${VENV_DIR}/bin/python"

# ── Dependency installation ───────────────────────────────────────────────────
REQS="${SCRIPT_DIR}/autoearn/requirements.txt"
if [[ ! -f "$REQS" ]]; then
    REQS="${SCRIPT_DIR}/requirements.txt"
fi

if [[ ! -f "${VENV_DIR}/.deps_installed" ]] || [[ "${UPDATE:-false}" == "true" ]]; then
    info "Installing dependencies from $REQS..."
    "$PYTHON" -m pip install --upgrade pip --quiet

    if [[ -f "$REQS" ]]; then
        "$PYTHON" -m pip install -r "$REQS" --quiet
    else
        # Minimal install
        "$PYTHON" -m pip install \
            groq google-generativeai huggingface_hub \
            fastapi uvicorn[standard] requests beautifulsoup4 \
            apscheduler praw --quiet
    fi

    # Try PyQt6 (optional — desktop UI)
    if [[ "$MODE" != "cli" ]]; then
        info "Installing PyQt6 for desktop UI..."
        "$PYTHON" -m pip install PyQt6 --quiet 2>/dev/null && \
            success "PyQt6 installed." || warn "PyQt6 not available — falling back to console mode."
    fi

    touch "${VENV_DIR}/.deps_installed"
    success "Dependencies installed."
fi

# ── Config check ─────────────────────────────────────────────────────────────
CONFIG="${SCRIPT_DIR}/autoearn/config.toml"
if [[ ! -f "$CONFIG" ]]; then
    warn "config.toml not found. Creating minimal config..."
    cat > "$CONFIG" <<'TOML_EOF'
[providers.groq]
api_key = ""  # Add your Groq API key here

[providers.gemini]
api_key = ""

[providers.ollama]
enabled = true
model = "llama3"

[dashboard]
port = 4200
host = "127.0.0.1"

[database]
path = "autoearn.db"

[ui]
theme = "dark"
TOML_EOF
    warn "Please edit autoearn/config.toml to add your API keys."
fi

# ── Seed agents (first run) ───────────────────────────────────────────────────
SEED_DONE="${SCRIPT_DIR}/.seed_done"
if [[ ! -f "$SEED_DONE" ]]; then
    info "Seeding agent definitions (first run)..."
    cd "$SCRIPT_DIR"
    "$PYTHON" autoearn/seed_agents.py 2>/dev/null && \
        touch "$SEED_DONE" && success "Agents seeded." || \
        warn "Could not seed agents (will retry on next run)."
fi

# ── Create log directory ──────────────────────────────────────────────────────
mkdir -p "$LOG_DIR"

# ── Launch ───────────────────────────────────────────────────────────────────
cd "$SCRIPT_DIR"
info "Starting AutoEarn in ${MODE} mode..."

LAUNCH_CMD="$PYTHON -m autoearn.desktop.app $EXTRA_ARGS"

if [[ "$MODE" == "no-gui" ]]; then
    # Background headless mode — write PID file
    nohup $LAUNCH_CMD >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    success "AutoEarn started in background (PID $(cat "$PID_FILE"))"
    info "Dashboard: http://localhost:4200"
    info "Logs: $LOG_FILE"
    info "Stop with: $0 --stop"
else
    # Foreground interactive mode
    exec $LAUNCH_CMD
fi
