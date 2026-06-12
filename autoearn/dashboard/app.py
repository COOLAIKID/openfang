"""FastAPI dashboard for the AutoEarn organization.

Read-only views plus a couple of control endpoints (manually trigger an agent,
edit a definition live). The HTML page polls the JSON endpoints, so the whole UI
is a single static template kept in ``templates/index.html``.
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from core import ai_client, database as db, message_bus

TEMPLATE = Path(__file__).resolve().parent / "templates" / "index.html"


def create_app(orchestrator) -> FastAPI:
    app = FastAPI(title="AutoEarn", docs_url="/docs")
    manager = orchestrator.manager

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return TEMPLATE.read_text(encoding="utf-8")

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "providers": ai_client.available_providers()}

    @app.get("/api/agents")
    def agents() -> list[dict]:
        status = db.all_status()
        out = []
        for a in manager.all():
            d = a.definition
            st = status.get(d["name"], {})
            out.append(
                {
                    "name": d["name"],
                    "role": d.get("role", "team"),
                    "team": d.get("team", ""),
                    "enabled": d.get("enabled", True),
                    "goal": d.get("goal", ""),
                    "model": d.get("model_preference") or "auto",
                    "interval_minutes": d.get("interval_minutes", 60),
                    "budget_usd": d.get("budget_usd", 0.0),
                    "tools": d.get("tools", []),
                    "last_run": st.get("last_run"),
                    "last_result": st.get("last_result"),
                    "runs": st.get("runs", 0),
                    "errors": st.get("errors", 0),
                }
            )
        out.sort(key=lambda x: ({"council": 0, "team": 1, "qc": 2}.get(x["role"], 3), x["team"], x["name"]))
        return out

    @app.get("/api/revenue")
    def revenue() -> dict:
        return db.revenue_summary()

    @app.get("/api/messages")
    def messages(limit: int = 50) -> list[dict]:
        return message_bus.recent(limit=limit)

    @app.get("/api/logs")
    def logs(limit: int = 60) -> list[dict]:
        return db.recent_activity(limit=limit)

    @app.post("/api/agents/{name}/trigger")
    def trigger(name: str) -> dict:
        if manager.get(name) is None:
            raise HTTPException(404, f"no such agent '{name}'")
        result = orchestrator.trigger_now(name)
        return {"name": name, "result": result}

    class DefinitionPatch(BaseModel):
        definition: dict

    @app.put("/api/agents/{name}")
    def update_agent(name: str, patch: DefinitionPatch) -> dict:
        agent = manager.get(name)
        if agent is None:
            raise HTTPException(404, f"no such agent '{name}'")
        agent.definition.update(patch.definition)
        agent.save()
        manager.reschedule(name)
        return {"name": name, "definition": agent.definition}

    class NewAgent(BaseModel):
        definition: dict

    @app.post("/api/agents")
    def create_agent(payload: NewAgent) -> dict:
        d = payload.definition
        if not d.get("name"):
            raise HTTPException(400, "definition.name required")
        if manager.get(d["name"]) is not None:
            raise HTTPException(409, f"agent '{d['name']}' already exists")
        agent = manager.spawn(d, created_by="dashboard")
        return {"name": agent.name, "definition": agent.definition}

    @app.get("/api/output")
    def output() -> list[dict]:
        root = Path(__file__).resolve().parent.parent / "output"
        items = []
        if root.exists():
            for p in sorted(root.rglob("*"), key=lambda x: x.stat().st_mtime if x.is_file() else 0, reverse=True):
                if p.is_file():
                    items.append(
                        {
                            "path": str(p.relative_to(root)),
                            "size": p.stat().st_size,
                            "modified": p.stat().st_mtime,
                        }
                    )
        return items[:200]

    return app
