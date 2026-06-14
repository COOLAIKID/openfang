#!/usr/bin/env bash
# Runs every time the Codespace starts (or resumes after sleep).
# Boots AutoEarn on port 4200 in the background so the dashboard is live.
set -euo pipefail

REPO=/workspaces/openfang
LOG="$REPO/.autoearn.log"
PID="$REPO/.autoearn.pid"

# Kill any old instance (Codespace resumed from sleep)
if [ -f "$PID" ] && kill -0 "$(cat "$PID")" 2>/dev/null; then
  echo "AutoEarn already running (pid $(cat "$PID"))"
  exit 0
fi

echo "Starting AutoEarn..."
cd "$REPO/autoearn"
nohup env HOST=0.0.0.0 PORT=4200 python main.py >"$LOG" 2>&1 &
echo $! >"$PID"

# Wait for the dashboard to come up (up to 30 s)
for i in $(seq 1 30); do
  if curl -sf http://localhost:4200/api/health >/dev/null 2>&1; then
    echo "AutoEarn is live on port 4200 ✓"
    exit 0
  fi
  sleep 1
done

echo "Dashboard didn't respond in 30 s — check $LOG"
tail -20 "$LOG"
