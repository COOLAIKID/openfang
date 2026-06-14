#!/usr/bin/env bash
# Runs on Codespace create AND every time you reconnect.
# Installs deps, starts AutoEarn, forces port 4200 to public.
set -euo pipefail

REPO=/workspaces/openfang
LOG="$REPO/.autoearn.log"
PID="$REPO/.autoearn.pid"

# Install deps (idempotent — fast if already installed)
pip install -q -r "$REPO/autoearn/requirements-cloud.txt"

# Kill any old instance
if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then
  kill "$(cat "$PID")" 2>/dev/null || true
  sleep 1
fi

# Start the server
cd "$REPO/autoearn"
nohup env HOST=0.0.0.0 PORT=4200 python main.py >"$LOG" 2>&1 &
echo $! >"$PID"

# Wait for it to be ready (up to 60 s)
echo "Waiting for AutoEarn to start..."
for i in $(seq 1 60); do
  if curl -sf http://localhost:4200/api/health >/dev/null 2>&1; then
    echo "AutoEarn is up ✓"
    break
  fi
  sleep 1
done

# Force the port to public so the URL works from any device
gh codespace ports visibility 4200:public \
  --codespace "${CODESPACE_NAME:-}" 2>/dev/null && \
  echo "Port 4200 is now public ✓" || \
  echo "Port visibility: set it to Public in the PORTS tab if needed"

echo ""
echo "==================================================="
echo "  Your dashboard URL:"
echo "  https://${CODESPACE_NAME}-4200.app.github.dev"
echo "==================================================="
