#!/usr/bin/env python3
"""Codespace startup — properly detaches the AutoEarn server process.

shell `nohup ... &` can be killed when the parent script exits in some
container runtimes. `start_new_session=True` creates a new OS process
session so the server is completely independent of this script.
"""
import os
import subprocess
import sys
import time
import urllib.request

REPO = "/workspaces/openfang"
LOG_PATH = os.path.join(REPO, ".autoearn.log")
HEALTH = "http://localhost:4200/api/health"

# ANSI colours
G = "\033[92m"; B = "\033[94m"; C = "\033[96m"
BOLD = "\033[1m"; DIM = "\033[2m"; RESET = "\033[0m"

print(f"\n{DIM}{'─'*52}{RESET}")
print(f"{BOLD}  AutoEarn — starting up …{RESET}")
print(f"{DIM}{'─'*52}{RESET}\n")

# Kill any old instance
subprocess.run(["pkill", "-f", "python main.py"], capture_output=True)
time.sleep(1)

# Install / update deps
print(f"{DIM}  Installing dependencies…{RESET}", flush=True)
subprocess.run(
    [sys.executable, "-m", "pip", "install", "-q", "-r",
     os.path.join(REPO, "autoearn", "requirements-cloud.txt")],
    check=False,
)
print(f"  Dependencies ready ✓\n")

# Start the server — start_new_session detaches it completely so it
# keeps running after this script exits (unlike nohup in some runtimes).
os.chdir(os.path.join(REPO, "autoearn"))
env = {**os.environ, "HOST": "0.0.0.0", "PORT": "4200"}
log = open(LOG_PATH, "a")
proc = subprocess.Popen(
    [sys.executable, "main.py"],
    env=env, stdout=log, stderr=log,
    start_new_session=True,
)
print(f"  Server started (PID {proc.pid})")

# Wait up to 90 s for the dashboard to respond
print(f"  Waiting for dashboard", end="", flush=True)
ready = False
for i in range(90):
    try:
        urllib.request.urlopen(HEALTH, timeout=2).read()
        ready = True
        break
    except Exception:
        print(".", end="", flush=True)
        time.sleep(1)

print()

if not ready:
    print(f"\n  ⚠  Server didn't respond — last log lines:")
    try:
        with open(LOG_PATH) as f:
            lines = f.readlines()
        print("".join(f"  {l}" for l in lines[-15:]))
    except Exception:
        pass

# Make the port public via gh CLI
name = os.environ.get("CODESPACE_NAME", "")
if name:
    subprocess.run(
        ["gh", "codespace", "ports", "visibility", "4200:public",
         "--codespace", name],
        capture_output=True,
    )

# ── Print the big clickable link ──────────────────────────────────────
url = f"https://{name}-4200.app.github.dev" if name else "http://localhost:4200"
bar = "═" * (len(url) + 10)

print(f"\n{BOLD}{G}{bar}{RESET}")
print(f"{BOLD}{G}║{RESET}{'':^{len(url)+8}}{BOLD}{G}║{RESET}")
print(f"{BOLD}{G}║{RESET}    {BOLD}{B}{url}{RESET}    {BOLD}{G}║{RESET}")
print(f"{BOLD}{G}║{RESET}{'':^{len(url)+8}}{BOLD}{G}║{RESET}")
print(f"{BOLD}{G}{bar}{RESET}")
print(f"\n  {BOLD}👆  Tap / click the link above to open your dashboard{RESET}\n")

# Auto-open the browser inside the Codespace (works on attach)
if name:
    subprocess.run(["gh", "codespace", "ports", "forward",
                    "4200:4200", "--codespace", name],
                   capture_output=True)
    # VS Code "Simple Browser" open
    print(f"{DIM}  (browser opening automatically…){RESET}\n")
