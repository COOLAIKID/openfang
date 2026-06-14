"""AutoEarn entrypoint.

Boots the SQLite store, loads every agent definition, starts agents, and serves
the dashboard on the configured host/port.

Two execution modes are selected automatically:

  Container mode (preferred)
    Each agent runs in its own Docker container via ``run_agent.py``.  Containers
    have ``restart: always`` so they live forever and survive crashes.  This mode
    is used when ``docker`` is available AND the ``autoearn:latest`` image exists.
    Build the image once with:  ``docker build -t autoearn:latest .``
    Or simply use:              ``docker compose up -d``

  Thread mode (fallback)
    Agents run as APScheduler background threads inside this process.  No Docker
    required.  Each agent ticks on its own ``interval_minutes`` cadence.

Set at least one free AI key first (in config.toml or via env):
    GROQ_API_KEY=... python main.py
"""
from __future__ import annotations

import uvicorn

from core import config, database as db
from core.agent_manager import AgentManager
from core.container_orchestrator import ContainerOrchestrator, is_available as docker_available
from core.scheduler import Orchestrator as ThreadOrchestrator
from dashboard.app import create_app


def build_app():
    db.init()
    manager = AgentManager()

    if docker_available():
        orchestrator = ContainerOrchestrator(manager)
        mode = "container (each agent in its own Linux container, loops forever)"
    else:
        orchestrator = ThreadOrchestrator(manager)
        mode = "thread (APScheduler — install Docker and build the image for container mode)"

    orchestrator.start()
    app = create_app(orchestrator)
    app.state.orchestrator = orchestrator
    app.state.mode = mode

    @app.on_event("shutdown")
    def _stop() -> None:
        orchestrator.shutdown()

    return app, orchestrator, mode


def main() -> None:
    app, orchestrator, mode = build_app()
    host = config.get("server", "host", "127.0.0.1")
    port = int(config.get("server", "port", 4200))

    n = len(orchestrator.manager.all())
    print(f"AutoEarn online — {n} agents — mode: {mode}")
    print(f"Dashboard → http://{host}:{port}")
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        orchestrator.shutdown()


if __name__ == "__main__":
    main()
