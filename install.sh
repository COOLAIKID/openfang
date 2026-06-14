#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  AutoEarn Installer — Linux / macOS
#  Run once to set up the full AutoEarn environment.
#
#  Usage:
#    curl -fsSL https://raw.githubusercontent.com/.../install.sh | bash
#    # or locally:
#    bash install.sh [--no-desktop] [--no-pyqt] [--dev]
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

INSTALL_DIR="${HOME}/.local/share/autoearn"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${HOME}/.local/share/applications"
ICON_DIR="${HOME}/.local/share/icons/hicolor"
REPO_URL="https://github.com/coolaikid/openfang"
PYTHON_MIN="3.10"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()    { echo -e "${CYAN}▶${NC} $*"; }
success() { echo -e "${GREEN}✓${NC} $*"; }
warn()    { echo -e "${YELLOW}⚠${NC} $*" >&2; }
error()   { echo -e "${RED}✗${NC} $*" >&2; }
step()    { echo -e "\n${BOLD}${BLUE}══ $* ══${NC}"; }

# ── Parse flags ───────────────────────────────────────────────────────────────
INSTALL_DESKTOP=true
INSTALL_PYQT=true
DEV_MODE=false

for arg in "$@"; do
    case "$arg" in
        --no-desktop) INSTALL_DESKTOP=false ;;
        --no-pyqt)    INSTALL_PYQT=false ;;
        --dev)        DEV_MODE=true ;;
        --help|-h)
            echo "Usage: bash install.sh [--no-desktop] [--no-pyqt] [--dev]"
            echo ""
            echo "  --no-desktop  Skip .desktop file and icon installation"
            echo "  --no-pyqt     Skip PyQt6 GUI installation (headless/CLI only)"
            echo "  --dev         Install in editable mode from current directory"
            exit 0
            ;;
    esac
done

# ── Header ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${BLUE}"
echo "   $$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$"
echo "   $$   AutoEarn v1.0 Installer     $$"
echo "   $$ Autonomous AI Organization    $$"
echo "   $$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$$"
echo -e "${NC}"

# ── System checks ─────────────────────────────────────────────────────────────
step "System Checks"

# OS
OS="$(uname -s)"
info "Operating system: $OS"

# Python
PYTHON=""
for cmd in python3.12 python3.11 python3.10 python3 python; do
    if command -v "$cmd" &>/dev/null; then
        VER=$("$cmd" -c "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}')" 2>/dev/null)
        if python3 -c "v=tuple(int(x) for x in '${VER}'.split('.')); exit(0 if v>=(3,10) else 1)" 2>/dev/null; then
            PYTHON="$cmd"
            break
        fi
    fi
done

if [[ -z "$PYTHON" ]]; then
    error "Python ${PYTHON_MIN}+ is required."
    echo ""
    echo "Install Python:"
    echo "  Ubuntu/Debian:  sudo apt install python3.11"
    echo "  Fedora:         sudo dnf install python3.11"
    echo "  Arch:           sudo pacman -S python"
    echo "  macOS:          brew install python@3.11"
    echo "  Download:       https://www.python.org/downloads/"
    exit 1
fi
success "Python found: $PYTHON ($("$PYTHON" --version))"

# pip
if ! "$PYTHON" -m pip --version &>/dev/null; then
    error "pip not found. Install with: $PYTHON -m ensurepip"
    exit 1
fi
success "pip found"

# git
if ! command -v git &>/dev/null; then
    warn "git not found. You won't be able to auto-update."
fi

# Display (for Qt)
if [[ "$INSTALL_PYQT" == "true" ]]; then
    if [[ -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" && "$OS" == "Linux" ]]; then
        warn "No DISPLAY detected. PyQt6 GUI may not be available."
        warn "You can still run AutoEarn in headless/CLI mode."
    fi
fi

# ── Installation directory ─────────────────────────────────────────────────────
step "Installation"

if [[ "$DEV_MODE" == "true" ]]; then
    INSTALL_DIR="$(pwd)"
    info "Dev mode: using current directory: $INSTALL_DIR"
else
    mkdir -p "$INSTALL_DIR"
    if [[ -d "${INSTALL_DIR}/.git" ]]; then
        info "Updating existing installation..."
        git -C "$INSTALL_DIR" pull --ff-only 2>/dev/null || warn "Could not auto-update (no network or conflicts)."
    elif command -v git &>/dev/null; then
        info "Cloning AutoEarn to $INSTALL_DIR..."
        git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
    else
        error "git is required to install AutoEarn (or use --dev from the repo)."
        exit 1
    fi
fi
success "Source installed at $INSTALL_DIR"

# ── Virtual environment ───────────────────────────────────────────────────────
step "Virtual Environment"

VENV_DIR="${INSTALL_DIR}/.venv"
if [[ ! -d "$VENV_DIR" ]]; then
    info "Creating virtual environment..."
    "$PYTHON" -m venv "$VENV_DIR"
fi
PYTHON_VENV="${VENV_DIR}/bin/python"
PIP="${VENV_DIR}/bin/pip"
source "${VENV_DIR}/bin/activate"
success "Virtual environment ready at $VENV_DIR"

# ── Core dependencies ─────────────────────────────────────────────────────────
step "Core Dependencies"

"$PIP" install --upgrade pip setuptools wheel --quiet
success "pip upgraded"

REQS="${INSTALL_DIR}/autoearn/requirements.txt"
[[ ! -f "$REQS" ]] && REQS="${INSTALL_DIR}/requirements.txt"

if [[ -f "$REQS" ]]; then
    info "Installing from $REQS..."
    "$PIP" install -r "$REQS" --quiet
    success "Core dependencies installed"
else
    info "Installing essential packages..."
    "$PIP" install \
        groq \
        google-generativeai \
        huggingface_hub \
        requests \
        beautifulsoup4 \
        fastapi \
        "uvicorn[standard]" \
        apscheduler \
        praw \
        --quiet
    success "Essential packages installed"
fi

# ── PyQt6 (optional desktop GUI) ──────────────────────────────────────────────
if [[ "$INSTALL_PYQT" == "true" ]]; then
    step "Desktop GUI (PyQt6)"
    info "Installing PyQt6..."
    if "$PIP" install PyQt6 --quiet 2>/dev/null; then
        success "PyQt6 installed — desktop GUI available"
    else
        warn "PyQt6 installation failed. AutoEarn will run in CLI/headless mode."
        warn "Try manually: pip install PyQt6"
    fi
fi

# ── Configuration ─────────────────────────────────────────────────────────────
step "Configuration"

CONFIG="${INSTALL_DIR}/autoearn/config.toml"
if [[ ! -f "$CONFIG" ]]; then
    info "Creating default config.toml..."
    cat > "$CONFIG" <<'TOML'
# AutoEarn Configuration
# See docs at https://github.com/coolaikid/openfang

[providers]
default_model = "groq/llama-3.3-70b-versatile"
fallback_model = "gemini/gemini-1.5-flash"

[providers.groq]
api_key = ""  # https://console.groq.com — free

[providers.gemini]
api_key = ""  # https://aistudio.google.com — free

[providers.huggingface]
token = ""    # https://huggingface.co/settings/tokens — free

[providers.mistral]
api_key = ""  # https://console.mistral.ai — free

[providers.ollama]
enabled = true
model   = "llama3"  # unlimited local fallback

[dashboard]
port = 4200
host = "127.0.0.1"

[database]
path = "autoearn.db"

[ui]
theme         = "dark"
show_splash   = true
minimize_to_tray = true

[logging]
level = "INFO"

[agents]
auto_start              = true
council_interval_minutes = 240

[notifications]
enabled     = true
duration_ms = 4000
TOML
    success "config.toml created"
    warn "ACTION REQUIRED: Add your API keys to ${CONFIG}"
else
    success "config.toml already exists (skipping)"
fi

# ── Seed agents ──────────────────────────────────────────────────────────────
step "Agent Setup"
info "Seeding agent definitions..."
cd "$INSTALL_DIR"
"$PYTHON_VENV" autoearn/seed_agents.py 2>/dev/null && success "Agents seeded" || warn "Could not seed agents."

# ── Desktop launcher ──────────────────────────────────────────────────────────
if [[ "$INSTALL_DESKTOP" == "true" ]]; then
    step "Desktop Integration"

    mkdir -p "$BIN_DIR" "$DESKTOP_DIR"

    # Launcher script in PATH
    LAUNCHER="${BIN_DIR}/autoearn"
    cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
# AutoEarn launcher (installed by install.sh)
exec "${INSTALL_DIR}/autoearn.sh" "\$@"
EOF
    chmod +x "$LAUNCHER"
    success "Launcher installed: $LAUNCHER"

    # .desktop file
    DESKTOP_FILE="${DESKTOP_DIR}/autoearn.desktop"
    cat > "$DESKTOP_FILE" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=AutoEarn
GenericName=AI Revenue Organization
Comment=Autonomous AI money-making organization
Exec=${LAUNCHER}
Icon=${INSTALL_DIR}/autoearn/desktop/assets/icon.svg
Terminal=false
StartupNotify=true
Categories=Finance;Office;Utility;
Keywords=ai;automation;revenue;agents;
EOF
    chmod +x "$DESKTOP_FILE"
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
    success ".desktop file installed"

    # Icons
    SVG_ICON="${INSTALL_DIR}/autoearn/desktop/assets/icon.svg"
    if [[ -f "$SVG_ICON" ]]; then
        for size in 16 32 48 64 128 256 512; do
            ICON_TARGET="${ICON_DIR}/${size}x${size}/apps"
            mkdir -p "$ICON_TARGET"
            if command -v rsvg-convert &>/dev/null; then
                rsvg-convert -w $size -h $size "$SVG_ICON" \
                    -o "${ICON_TARGET}/autoearn.png" 2>/dev/null || true
            elif command -v inkscape &>/dev/null; then
                inkscape -w $size -h $size "$SVG_ICON" \
                    -o "${ICON_TARGET}/autoearn.png" 2>/dev/null || true
            fi
        done
        gtk-update-icon-cache "$ICON_DIR" 2>/dev/null || true
        cp "$SVG_ICON" "${ICON_DIR}/scalable/apps/autoearn.svg" 2>/dev/null || true
        success "Icons installed"
    fi
fi

# ── PATH reminder ─────────────────────────────────────────────────────────────
if [[ "$INSTALL_DESKTOP" == "true" ]] && ! echo "$PATH" | grep -q "$BIN_DIR"; then
    echo ""
    warn "Add ${BIN_DIR} to your PATH to use 'autoearn' from anywhere:"
    echo "    echo 'export PATH=\"\${HOME}/.local/bin:\${PATH}\"' >> ~/.bashrc"
    echo "    source ~/.bashrc"
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${GREEN}║   AutoEarn installation complete! 🎉    ║${NC}"
echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════╝${NC}"
echo ""
echo "  Next steps:"
echo "  1. Add API keys:   nano ${CONFIG}"
echo "  2. Start AutoEarn: ${INSTALL_DIR}/autoearn.sh"
echo "  3. Open dashboard: http://localhost:4200"
echo ""
echo "  Modes:"
echo "    GUI:       ${INSTALL_DIR}/autoearn.sh"
echo "    Headless:  ${INSTALL_DIR}/autoearn.sh --no-gui"
echo "    Console:   ${INSTALL_DIR}/autoearn.sh --cli"
echo ""
