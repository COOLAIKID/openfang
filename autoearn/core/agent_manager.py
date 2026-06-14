"""Loads agent definition files and manages their lifecycle.

The manager discovers every ``*.json`` under ``council/``, ``teams/`` and ``qc/``,
wraps each in an :class:`~core.agent_base.Agent`, and keeps them in a registry.
It collaborates with the scheduler (each agent runs on its own interval) and with
the self-modification tools (which can spawn/kill/reschedule agents at runtime).
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable

from . import self_tools
from .agent_base import Agent

ROOT = Path(__file__).resolve().parent.parent
AGENT_DIRS = {
    "council": ROOT / "council",
    "team": ROOT / "teams",
    "qc": ROOT / "qc",
}


class AgentManager:
    def __init__(self) -> None:
        self._agents: dict[str, Agent] = {}
        self._lock = threading.Lock()
        # Scheduler wires these in so the manager can (re)schedule agents.
        self.on_schedule: Callable[[Agent], None] | None = None
        self.on_unschedule: Callable[[str], None] | None = None
        self_tools.bind_manager(self)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------
    def discover(self) -> None:
        """Load every agent definition from disk into the registry."""
        with self._lock:
            self._agents.clear()
            for base in AGENT_DIRS.values():
                if not base.exists():
                    continue
                for path in sorted(base.rglob("*.json")):
                    try:
                        agent = Agent.load(path)
                        self._agents[agent.name] = agent
                    except Exception as exc:  # noqa: BLE001
                        print(f"[manager] failed to load {path}: {exc}")

    # ------------------------------------------------------------------
    # Registry access
    # ------------------------------------------------------------------
    def get(self, name: str) -> Agent | None:
        return self._agents.get(name)

    def all(self) -> list[Agent]:
        return list(self._agents.values())

    def enabled(self) -> list[Agent]:
        return [a for a in self._agents.values() if a.enabled]

    # ------------------------------------------------------------------
    # Runtime mutations (called by self_tools)
    # ------------------------------------------------------------------
    def spawn(self, definition: dict[str, Any], created_by: str = "system") -> Agent:
        role = definition.get("role", "team")
        base = AGENT_DIRS.get(role, AGENT_DIRS["team"])
        team = definition.get("team", "")
        target_dir = base / team if (role == "team" and team) else base
        target_dir.mkdir(parents=True, exist_ok=True)
        path = target_dir / f"{definition['name']}.json"
        agent = Agent(path, definition)
        agent.save()
        with self._lock:
            self._agents[agent.name] = agent
        if self.on_schedule:
            self.on_schedule(agent)
        return agent

    def kill(self, name: str) -> None:
        agent = self._agents.get(name)
        if agent is None:
            return
        agent.definition["enabled"] = False
        agent.save()
        if self.on_unschedule:
            self.on_unschedule(name)

    def reschedule(self, name: str) -> None:
        """Re-apply an agent's schedule after its interval/enabled changed."""
        agent = self._agents.get(name)
        if agent is None:
            return
        if self.on_unschedule:
            self.on_unschedule(name)
        if agent.enabled and self.on_schedule:
            self.on_schedule(agent)
