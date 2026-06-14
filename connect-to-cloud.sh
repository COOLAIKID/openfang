#!/usr/bin/env bash
# Connect THIS computer to your cloud AutoEarn dashboard.
# It dials out to your dashboard (works behind any home Wi-Fi) so the dashboard
# can run agents and tasks on this machine. Leave it running.
#
#   ./connect-to-cloud.sh                       # will prompt for URL + password
#   AUTOEARN_CLOUD_URL=https://x.onrender.com AUTOEARN_PASSWORD=pw ./connect-to-cloud.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"

URL="${AUTOEARN_CLOUD_URL:-}"
PW="${AUTOEARN_PASSWORD:-}"
[ -z "$URL" ] && read -r -p "Your dashboard URL (e.g. https://autoearn-xxxx.onrender.com): " URL
[ -z "$PW" ]  && read -r -s -p "Your dashboard password: " PW && echo

# The connector only needs the standard library, but we reuse the venv if present.
PY="$VENV/bin/python"; [ -x "$PY" ] || PY="$(command -v python3 || command -v python)"

NAME="${AUTOEARN_MACHINE:-$(hostname)}"
echo "Connecting '$NAME' to $URL …  (Ctrl+C to stop)"
cd "$ROOT/autoearn"
exec env AUTOEARN_CLOUD_URL="$URL" AUTOEARN_PASSWORD="$PW" AUTOEARN_MACHINE="$NAME" "$PY" run_runner.py
