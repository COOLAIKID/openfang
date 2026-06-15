#!/usr/bin/env python3
"""Codespace startup — runs via postStartCommand every time the container starts.

Uses start_new_session=True so the server process is completely independent
of this script and keeps running after this script exits.
"""
import os
import subprocess
import sys
import time
import urllib.request

REPO   = "/workspaces/openfang"
LOG    = os.path.join(REPO, ".autoearn.log")
HEALTH = "http://localhost:4200/api/health"

# ANSI colours (gracefully ignored if terminal doesn't support them)
G    = "\033[92m"
B    = "\033[94m"
BOLD = "\033[1m"
DIM  = "\033[2m"
RST  = "\033[0m"


def server_healthy() -> bool:
    try:
        urllib.request.urlopen(HEALTH, timeout=3).read()
        return True
    except Exception:
        return False


print(f"\n{DIM}{'─'*52}{RST}")
print(f"{BOLD}  AutoEarn — starting up …{RST}")
print(f"{DIM}{'─'*52}{RST}\n")

# ── If already running and healthy, skip the restart ─────────────────
if server_healthy():
    print(f"  Server already running ✓  (skipping restart)\n")
else:
    # Kill stale process if any
    subprocess.run(["pkill", "-f", "python main.py"], capture_output=True)
    time.sleep(1)

    # Install / update deps (fast no-op if nothing changed)
    print(f"{DIM}  Installing dependencies…{RST}", flush=True)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-r",
         os.path.join(REPO, "autoearn", "requirements-cloud.txt")],
        check=False,
    )
    print(f"  Dependencies ready ✓\n")

    # Start the server in its own process session so it outlives this script
    os.chdir(os.path.join(REPO, "autoearn"))
    env = {**os.environ, "HOST": "0.0.0.0", "PORT": "4200"}
    with open(LOG, "a") as log_fh:
        proc = subprocess.Popen(
            [sys.executable, "main.py"],
            env=env,
            stdout=log_fh,
            stderr=log_fh,
            start_new_session=True,
        )
    print(f"  Server started (PID {proc.pid})")

    # Wait up to 90 s for the dashboard to respond
    print(f"  Waiting for dashboard", end="", flush=True)
    for _ in range(90):
        if server_healthy():
            break
        print(".", end="", flush=True)
        time.sleep(1)
    print()

    if not server_healthy():
        print(f"\n  ⚠  Server didn't respond — last log lines:")
        try:
            with open(LOG) as f:
                print("".join(f"  {l}" for l in f.readlines()[-15:]))
        except Exception:
            pass
        print()

# ── Set port visibility to public via gh CLI ─────────────────────────
name = os.environ.get("CODESPACE_NAME", "")
if name:
    try:
        subprocess.run(
            ["gh", "codespace", "ports", "visibility", "4200:public",
             "--codespace", name],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass   # gh not available or errored — devcontainer visibility handles it

# ── Print the big clickable dashboard link ───────────────────────────
url = f"https://{name}-4200.app.github.dev" if name else "http://localhost:4200"
bar = "═" * (len(url) + 10)

print(f"\n{BOLD}{G}{bar}{RST}")
print(f"{BOLD}{G}║{RST}{'':^{len(url)+8}}{BOLD}{G}║{RST}")
print(f"{BOLD}{G}║{RST}    {BOLD}{B}{url}{RST}    {BOLD}{G}║{RST}")
print(f"{BOLD}{G}║{RST}{'':^{len(url)+8}}{BOLD}{G}║{RST}")
print(f"{BOLD}{G}{bar}{RST}")
print(f"\n  {BOLD}👆  Tap / click the link above to open your dashboard{RST}\n")
