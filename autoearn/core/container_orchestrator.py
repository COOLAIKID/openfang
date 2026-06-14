"""Container orchestrator — one Docker container per agent, forever.

This replaces the APScheduler-based ``Orchestrator`` when Docker is available.
Each agent runs in its own isolated Linux container via ``run_agent.py``.
Containers have ``restart: always`` so they survive crashes automatically.

If Docker is not available, ``is_available()`` returns False and ``main.py``
falls back to the thread-based scheduler seamlessly.

Container names: ``ae_<agent_name>``  (matches docker-compose naming)
"""
from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = ROOT / "output"
IMAGE_NAME = "autoearn:latest"
CONTAINER_PREFIX = "ae_"
DB_PATH = ROOT / "autoearn.db"


# --------------------------------------------------------------------------
# Docker helpers
# --------------------------------------------------------------------------

def _docker(*args: str, stdin: str | None = None, timeout: int = 60) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["docker", *args],
            input=stdin, capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except FileNotFoundError:
        return 127, "", "docker not found"
    except subprocess.TimeoutExpired:
        return 1, "", "timeout"


def is_available() -> bool:
    """Return True if the Docker daemon is reachable and the image is built."""
    rc, _, _ = _docker("info", "--format", "{{.ServerVersion}}", timeout=8)
    if rc != 0:
        return False
    rc2, _, _ = _docker("image", "inspect", IMAGE_NAME, timeout=8)
    return rc2 == 0


# --------------------------------------------------------------------------
# ContainerOrchestrator
# --------------------------------------------------------------------------

class ContainerOrchestrator:
    """Manages one long-running Docker container per agent.

    The ``Orchestrator`` interface is compatible with the scheduler-based one:
    ``start()``, ``shutdown()``, ``trigger_now()``, ``schedule_agent()``,
    ``unschedule_agent()``.  The ``manager`` attribute exposes the same
    ``AgentManager`` so the dashboard works unchanged.
    """

    def __init__(self, manager) -> None:  # type: ignore[type-arg]
        self.manager = manager
        self._lock = threading.Lock()
        self._monitor_thread: threading.Thread | None = None
        self._running = False
        manager.on_schedule = self.schedule_agent
        manager.on_unschedule = self.unschedule_agent

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        self.manager.discover()
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        for agent in self.manager.enabled():
            self._ensure_container(agent.name)

        self._running = True
        self._monitor_thread = threading.Thread(
            target=self._monitor_loop, daemon=True, name="container-monitor"
        )
        self._monitor_thread.start()

    def shutdown(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Scheduler-compatible interface
    # ------------------------------------------------------------------

    def schedule_agent(self, agent) -> None:  # type: ignore[type-arg]
        if agent.enabled:
            self._ensure_container(agent.name)

    def unschedule_agent(self, name: str) -> None:
        self._stop_container(name)

    def trigger_now(self, name: str) -> str:
        """Run the agent once immediately (in-thread, for dashboard manual trigger)."""
        agent = self.manager.get(name)
        if agent is None:
            return f"ERROR: no such agent '{name}'."
        return agent.run()

    # ------------------------------------------------------------------
    # Container management
    # ------------------------------------------------------------------

    def _container_name(self, agent_name: str) -> str:
        safe = "".join(c for c in agent_name.lower() if c.isalnum() or c == "_")
        return f"{CONTAINER_PREFIX}{safe}"

    def _container_running(self, cname: str) -> bool:
        rc, state, _ = _docker(
            "inspect", "--format", "{{.State.Running}}", cname, timeout=10
        )
        return rc == 0 and state.strip() == "true"

    def _ensure_container(self, agent_name: str) -> None:
        cname = self._container_name(agent_name)
        with self._lock:
            rc, state, _ = _docker(
                "inspect", "--format", "{{.State.Running}}", cname, timeout=10
            )
            if rc == 0:
                # Container exists
                if state.strip() != "true":
                    _docker("start", cname, timeout=30)
                return
            # Create a new container
            self._create_container(agent_name, cname)

    def _create_container(self, agent_name: str, cname: str) -> None:
        DB_PATH.touch(exist_ok=True)
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        rc, _, err = _docker(
            "run", "-d",
            "--name", cname,
            "--restart", "always",
            "--memory", "256m",
            "--cpus", "0.5",
            "--network", "bridge",
            # Shared DB (SQLite WAL — concurrent-safe)
            "-v", f"{DB_PATH}:/app/autoearn.db:rw",
            # Shared output directory
            "-v", f"{OUTPUT_DIR}:/app/output:rw",
            # Agent JSON definitions (read-write so self-edits survive reboots)
            "-v", f"{ROOT / 'council'}:/app/council:rw",
            "-v", f"{ROOT / 'teams'}:/app/teams:rw",
            "-v", f"{ROOT / 'qc'}:/app/qc:rw",
            # Pass through AI API keys from the host environment
            *_env_flags(),
            "-e", f"AGENT_NAME={agent_name}",
            "--workdir", "/app",
            IMAGE_NAME,
            "python", "run_agent.py", agent_name,
            timeout=60,
        )
        if rc != 0:
            print(f"[orchestrator] WARNING: could not start container {cname}: {err}")

    def _stop_container(self, agent_name: str) -> None:
        cname = self._container_name(agent_name)
        _docker("rm", "-f", cname, timeout=30)

    # ------------------------------------------------------------------
    # Health monitor — restart dead containers every 30 s
    # ------------------------------------------------------------------

    def _monitor_loop(self) -> None:
        while self._running:
            for agent in self.manager.enabled():
                cname = self._container_name(agent.name)
                if not self._container_running(cname):
                    print(f"[orchestrator] restarting dead container: {cname}")
                    self._ensure_container(agent.name)
            time.sleep(30)

    # ------------------------------------------------------------------
    # Dashboard status helpers
    # ------------------------------------------------------------------

    def container_statuses(self) -> list[dict[str, Any]]:
        """Return status of all autoearn containers for the dashboard."""
        rc, out, _ = _docker(
            "ps", "-a",
            "--filter", f"name={CONTAINER_PREFIX}",
            "--format", "{{.Names}}\t{{.Status}}\t{{.Size}}",
            timeout=15,
        )
        if rc != 0 or not out:
            return []
        rows: list[dict[str, Any]] = []
        for line in out.strip().splitlines():
            parts = line.split("\t")
            rows.append({
                "container": parts[0] if parts else "?",
                "status": parts[1] if len(parts) > 1 else "?",
                "size": parts[2] if len(parts) > 2 else "?",
            })
        return rows

    def agent_logs(self, agent_name: str, lines: int = 50) -> str:
        """Return the last N log lines from the agent's container."""
        cname = self._container_name(agent_name)
        rc, out, err = _docker("logs", "--tail", str(lines), cname, timeout=15)
        return (out + "\n" + err).strip() if rc == 0 else f"ERROR: {err}"


# --------------------------------------------------------------------------
# Helper
# --------------------------------------------------------------------------

_API_KEY_ENVS = [
    "GROQ_API_KEY", "GOOGLE_API_KEY", "HUGGINGFACE_API_TOKEN",
    "MISTRAL_API_KEY", "WP_URL", "WP_USER", "WP_APP_PASSWORD",
    "MEDIUM_TOKEN", "TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID",
    "REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET", "REDDIT_USERNAME",
    "REDDIT_PASSWORD", "REDDIT_USER_AGENT",
]


def _env_flags() -> list[str]:
    """Return ``-e KEY=VALUE`` pairs for all known API keys set in the host env."""
    import os
    flags: list[str] = []
    for key in _API_KEY_ENVS:
        val = os.environ.get(key)
        if val:
            flags.extend(["-e", f"{key}={val}"])
    return flags
