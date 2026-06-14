"""Single-agent forever-loop runner.

Each agent container runs this script:
    python run_agent.py <agent_name>

The script finds the agent's JSON definition, initialises the shared DB,
then loops forever:
    1. Re-read the JSON file (picks up any self-modifications).
    2. Run one full Observe → Reason → Act → Reflect cycle.
    3. Sleep for ``interval_minutes`` (from the freshly-read definition).
    4. Goto 1.

If the agent is marked disabled, the process sleeps 60 s and re-checks.
Docker's ``restart: always`` policy ensures the process comes back if it
ever exits unexpectedly.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Allow running as ``python run_agent.py`` from inside the container where
# the working directory is /app (or the project root).
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from core import database as db                   # noqa: E402
from core.agent_base import Agent                 # noqa: E402

AGENT_DIRS = [
    ROOT / "council",
    ROOT / "teams",
    ROOT / "qc",
]

MIN_INTERVAL_SECONDS = 60  # never spin faster than once a minute


def _find_agent_path(name: str) -> Path | None:
    for base in AGENT_DIRS:
        for path in base.rglob(f"{name}.json"):
            return path
    return None


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python run_agent.py <agent_name>", file=sys.stderr)
        sys.exit(1)

    name = sys.argv[1]
    path = _find_agent_path(name)
    if path is None:
        print(f"ERROR: agent '{name}' not found under council/, teams/, or qc/", file=sys.stderr)
        sys.exit(1)

    db.init()
    db.log_activity(name, "startup", f"container started pid={os.getpid()}")
    print(f"[{name}] container online — looping forever (Ctrl-C to stop)")

    while True:
        try:
            agent = Agent.load(path)  # re-read JSON every tick → picks up self-edits
        except Exception as exc:
            print(f"[{name}] ERROR loading definition: {exc}", file=sys.stderr)
            time.sleep(60)
            continue

        if not agent.enabled:
            print(f"[{name}] disabled — sleeping 60 s")
            time.sleep(60)
            continue

        try:
            result = agent.run()
            print(f"[{name}] {result[:120]}")
        except Exception as exc:  # noqa: BLE001
            print(f"[{name}] unhandled error: {exc}", file=sys.stderr)
            db.log_activity(name, "fatal_error", str(exc)[:400])

        interval = max(agent.interval_minutes * 60, MIN_INTERVAL_SECONDS)
        print(f"[{name}] sleeping {interval // 60} min")
        time.sleep(interval)


if __name__ == "__main__":
    main()
