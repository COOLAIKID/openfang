"""Entry point for the home connector.

Run on your own computer so it works for your cloud dashboard:

    AUTOEARN_CLOUD_URL=https://your-app.onrender.com \
    AUTOEARN_PASSWORD=your_password \
    python run_runner.py

Optional: AUTOEARN_MACHINE="Aaron's MacBook" to name this computer.
"""
from __future__ import annotations

import os
import sys

from runner import Runner


def main() -> None:
    base = os.environ.get("AUTOEARN_CLOUD_URL") or (sys.argv[1] if len(sys.argv) > 1 else "")
    if not base:
        print("Set AUTOEARN_CLOUD_URL to your dashboard URL, e.g.")
        print("  AUTOEARN_CLOUD_URL=https://your-app.onrender.com "
              "AUTOEARN_PASSWORD=... python run_runner.py")
        sys.exit(1)
    Runner(
        base,
        password=os.environ.get("AUTOEARN_PASSWORD", ""),
        machine=os.environ.get("AUTOEARN_MACHINE"),
    ).loop()


if __name__ == "__main__":
    main()
