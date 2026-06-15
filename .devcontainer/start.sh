#!/usr/bin/env bash
# Runs on Codespace create AND every time you reconnect.
# Designed to never crash — every command is safe to fail.

REPO=/workspaces/openfang
LOG="$REPO/.autoearn.log"

echo "=== AutoEarn startup ==="

# Kill any old instance cleanly
pkill -f "python main.py" 2>/dev/null || true
sleep 1

# Install / update deps (fast if nothing changed)
pip install -q -r "$REPO/autoearn/requirements-cloud.txt" 2>&1 | tail -3

# Start the server in the background
cd "$REPO/autoearn"
HOST=0.0.0.0 PORT=4200 nohup python main.py >> "$LOG" 2>&1 &
SERVER_PID=$!
echo "$SERVER_PID" > "$REPO/.autoearn.pid"
echo "Server started (PID $SERVER_PID)"

# Wait up to 60 s for the dashboard to respond
echo "Waiting for dashboard..."
READY=0
for i in $(seq 1 60); do
  if curl -sf http://localhost:4200/api/health >/dev/null 2>&1; then
    READY=1
    break
  fi
  sleep 1
done

if [ "$READY" -eq 0 ]; then
  echo "Server didn't respond — last 20 lines of log:"
  tail -20 "$LOG"
fi

# Try to set port visibility to public via gh CLI
gh codespace ports visibility 4200:public \
  --codespace "${CODESPACE_NAME:-}" 2>/dev/null \
  && echo "Port 4200 set to public ✓" \
  || echo "(Set port 4200 to Public in the PORTS tab if needed)"

echo ""
echo "================================================="
echo "  Dashboard → https://${CODESPACE_NAME:-CODESPACE_NAME}-4200.app.github.dev"
echo "================================================="
