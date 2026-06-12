"""AutoEarn entrypoint.

Boots the SQLite store, loads every agent definition, starts the background
scheduler (each agent ticks on its own interval), and serves the dashboard +
control API on the configured host/port.

    python main.py

Set at least one free AI key first (in config.toml or via env, e.g.
``GROQ_API_KEY=... python main.py``). With no cloud key it will fall back to a
local Ollama install if one is running.
"""
from __future__ import annotations

import uvicorn

from core import config, database as db
from core.agent_manager import AgentManager
from core.scheduler import Orchestrator
from dashboard.app import create_app


def build_app():
    db.init()
    manager = AgentManager()
    orchestrator = Orchestrator(manager)
    orchestrator.start()
    app = create_app(orchestrator)
    app.state.orchestrator = orchestrator

    @app.on_event("shutdown")
    def _stop() -> None:
        orchestrator.shutdown()

    return app, orchestrator


def main() -> None:
    app, orchestrator = build_app()
    host = config.get("server", "host", "127.0.0.1")
    port = int(config.get("server", "port", 4200))

    n = len(orchestrator.manager.all())
    print(f"AutoEarn online: {n} agents loaded. Dashboard → http://{host}:{port}")
    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    finally:
        orchestrator.shutdown()


if __name__ == "__main__":
    main()
