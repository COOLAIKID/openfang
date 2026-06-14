"""APScheduler wrapper that ticks each agent on its own interval.

Agents own their cadence (``interval_minutes`` in their JSON). When an agent
self-modifies its interval, the manager calls back into :meth:`schedule_agent`
to re-register the job. A small stagger on first run keeps 21+ agents from all
firing in the same second on startup.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from apscheduler.schedulers.background import BackgroundScheduler

from .agent_base import Agent
from .agent_manager import AgentManager


class Orchestrator:
    def __init__(self, manager: AgentManager) -> None:
        self.manager = manager
        self.scheduler = BackgroundScheduler(job_defaults={"max_instances": 1, "coalesce": True})
        manager.on_schedule = self.schedule_agent
        manager.on_unschedule = self.unschedule_agent

    def start(self) -> None:
        self.manager.discover()
        for agent in self.manager.enabled():
            self.schedule_agent(agent)
        self.scheduler.start()

    def shutdown(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    # ------------------------------------------------------------------
    def schedule_agent(self, agent: Agent) -> None:
        job_id = f"agent:{agent.name}"
        # Stagger the first run so startup doesn't stampede the providers.
        first = datetime.now() + timedelta(seconds=random.randint(2, 45))
        self.scheduler.add_job(
            self._tick,
            "interval",
            minutes=max(agent.interval_minutes, 1),
            id=job_id,
            args=[agent.name],
            next_run_time=first,
            replace_existing=True,
        )

    def unschedule_agent(self, name: str) -> None:
        job_id = f"agent:{name}"
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)

    def trigger_now(self, name: str) -> str:
        """Run an agent immediately, in-thread, and return its result."""
        agent = self.manager.get(name)
        if agent is None:
            return f"ERROR: no such agent '{name}'."
        return agent.run()

    # ------------------------------------------------------------------
    def _tick(self, name: str) -> None:
        agent = self.manager.get(name)
        if agent is None or not agent.enabled:
            return
        agent.run()
