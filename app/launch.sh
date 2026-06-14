#!/usr/bin/env bash
# AutoEarn launcher (macOS / Linux).
# Clicking the app icon runs this: start the server if it isn't already up,
# wait until it's ready, then open the dashboard in the default browser.
set -euo pipefail

# Repo root = parent of this script's dir.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
VENV="$ROOT/.venv"
PORT="${SERVER_PORT:-4200}"
URL="http://localhost:$PORT"
LOG="$ROOT/.autoearn.log"

open_browser() {
  [ "${NO_OPEN:-0}" = "1" ] && return 0
  if command -v open >/dev/null 2>&1; then open "$URL"          # macOS
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL" # Linux
  fi
}

is_up() { curl -fsS "$URL/api/health" >/dev/null 2>&1; }

# Already running? Just open it.
if is_up; then open_browser; exit 0; fi

# First run: create venv + install slim deps.
if [ ! -x "$VENV/bin/python" ]; then
  PY="$(command -v python3 || command -v python)"
  "$PY" -m venv "$VENV"
  "$VENV/bin/python" -m pip install --quiet --upgrade pip
  "$VENV/bin/python" -m pip install --quiet -r "$ROOT/autoearn/requirements-cloud.txt"
fi

# Start the server in the background (must run from autoearn/ for imports).
cd "$ROOT/autoearn"
HOST=127.0.0.1 PORT="$PORT" nohup "$VENV/bin/python" main.py >>"$LOG" 2>&1 &
echo $! > "$ROOT/.autoearn.pid"

# Wait up to ~30s for it to come up, then open the browser.
for _ in $(seq 1 60); do
  if is_up; then open_browser; exit 0; fi
  sleep 0.5
done

echo "AutoEarn did not start in time — see $LOG" >&2
exit 1
