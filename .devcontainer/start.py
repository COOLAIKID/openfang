#!/usr/bin/env python3
"""Codespace startup.

Key design:
- Start the server FIRST so port 4200 is up within seconds.
- Run pip install only if server fails to start (deps missing).
- Never block on anything that can hang.
"""
import os
import subprocess
import sys
import time
import urllib.request

REPO   = "/workspaces/openfang"
LOG    = os.path.join(REPO, ".autoearn.log")
HEALTH = "http://localhost:4200/api/health"
REQS   = os.path.join(REPO, "autoearn", "requirements-cloud.txt")

G    = "\033[92m"
B    = "\033[94m"
BOLD = "\033[1m"
DIM  = "\033[2m"
RST  = "\033[0m"


def server_healthy(timeout=3):
    try:
        urllib.request.urlopen(HEALTH, timeout=timeout).read()
        return True
    except Exception:
        return False


def pip_install():
    print(f"{DIM}  Installing dependencies…{RST}", flush=True)
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "-q", "-r", REQS],
        check=False,
    )
    print(f"  Dependencies ready ✓")


def start_server():
    server_dir = os.path.join(REPO, "autoearn")
    env = {**os.environ, "HOST": "0.0.0.0", "PORT": "4200"}
    with open(LOG, "a") as lf:
        proc = subprocess.Popen(
            [sys.executable, os.path.join(server_dir, "main.py")],
            env=env, stdout=lf, stderr=lf,
            start_new_session=True,
        )
    return proc


print(f"\n{DIM}{'─'*52}{RST}")
print(f"{BOLD}  AutoEarn — starting up …{RST}")
print(f"{DIM}{'─'*52}{RST}\n")

# Already running? Skip everything.
if server_healthy():
    print(f"  Server already running ✓\n")
else:
    # Kill any stale server. Pattern matches "/workspaces/openfang/autoearn/main.py"
    subprocess.run(["pkill", "-f", "autoearn/main.py"], capture_output=True)
    time.sleep(1)

    # Attempt 1: start immediately (deps installed by onCreateCommand)
    print(f"  Starting server…", flush=True)
    proc = start_server()
    print(f"  Server process started (PID {proc.pid})")
    print(f"  Waiting for dashboard", end="", flush=True)

    ready = False
    for _ in range(20):
        if server_healthy():
            ready = True
            break
        print(".", end="", flush=True)
        time.sleep(1)
    print()

    # Attempt 2: deps might be missing — install then retry
    if not ready:
        print(f"\n  Server not ready — installing/updating deps…")
        subprocess.run(["pkill", "-f", "autoearn/main.py"], capture_output=True)
        pip_install()
        proc = start_server()
        print(f"  Restarted (PID {proc.pid})")
        print(f"  Waiting for dashboard", end="", flush=True)
        for _ in range(60):
            if server_healthy():
                ready = True
                break
            print(".", end="", flush=True)
            time.sleep(1)
        print()

    if not ready:
        print(f"\n  ⚠  Server didn't respond. Last log lines:")
        try:
            with open(LOG) as f:
                print("".join(f"  {l}" for l in f.readlines()[-20:]))
        except Exception:
            pass

# Set port to public
name = os.environ.get("CODESPACE_NAME", "")
if name:
    try:
        subprocess.run(
            ["gh", "codespace", "ports", "visibility", "4200:public",
             "--codespace", name],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass

# Print big clickable link
url = f"https://{name}-4200.app.github.dev" if name else "http://localhost:4200"
bar = "═" * (len(url) + 10)
print(f"\n{BOLD}{G}{bar}{RST}")
print(f"{BOLD}{G}║{RST}{'':^{len(url)+8}}{BOLD}{G}║{RST}")
print(f"{BOLD}{G}║{RST}    {BOLD}{B}{url}{RST}    {BOLD}{G}║{RST}")
print(f"{BOLD}{G}║{RST}{'':^{len(url)+8}}{BOLD}{G}║{RST}")
print(f"{BOLD}{G}{bar}{RST}")
print(f"\n  {BOLD}👆  Tap / click the link above to open your dashboard{RST}\n")
