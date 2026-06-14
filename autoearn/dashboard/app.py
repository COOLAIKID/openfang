"""FastAPI dashboard for the AutoEarn organization.

Read-only views plus a couple of control endpoints (manually trigger an agent,
edit a definition live). The HTML page polls the JSON endpoints, so the whole UI
is a single static template kept in ``templates/index.html``.
"""
from __future__ import annotations

import json
import socket
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from core import ai_client, connectors, database as db, message_bus, providers, sandbox, skills
from core.chat import Chat
from core.container_orchestrator import ContainerOrchestrator
from core.toolkit.computer import active_browsers

TEMPLATE = Path(__file__).resolve().parent / "templates" / "index.html"


# Request bodies (module scope so FastAPI/pydantic resolve them as body models).
class DefinitionPatch(BaseModel):
    definition: dict


class NewAgent(BaseModel):
    definition: dict


class ChatMessage(BaseModel):
    message: str
    agent: str = "ceo"


class SkillSource(BaseModel):
    source: str


class SkillRun(BaseModel):
    input: str = ""


def create_app(orchestrator) -> FastAPI:
    app = FastAPI(title="AutoEarn", docs_url="/docs")
    manager = orchestrator.manager
    chat = Chat(orchestrator)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return TEMPLATE.read_text(encoding="utf-8")

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "providers": ai_client.available_providers()}

    @app.get("/api/providers")
    def providers_list() -> list[dict]:
        return [p.describe() for p in providers.instantiate_all()]

    @app.get("/api/connectors")
    def connectors_list() -> list[dict]:
        return connectors.configured()

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

    @app.put("/api/agents/{name}")
    def update_agent(name: str, patch: DefinitionPatch) -> dict:
        agent = manager.get(name)
        if agent is None:
            raise HTTPException(404, f"no such agent '{name}'")
        agent.definition.update(patch.definition)
        agent.save()
        manager.reschedule(name)
        return {"name": name, "definition": agent.definition}

    @app.post("/api/agents")
    def create_agent(payload: NewAgent) -> dict:
        d = payload.definition
        if not d.get("name"):
            raise HTTPException(400, "definition.name required")
        if manager.get(d["name"]) is not None:
            raise HTTPException(409, f"agent '{d['name']}' already exists")
        agent = manager.spawn(d, created_by="dashboard")
        return {"name": agent.name, "definition": agent.definition}

    # ---- Chat ----------------------------------------------------------
    @app.post("/api/chat")
    def chat_send(payload: ChatMessage) -> dict:
        return chat.handle(payload.message, agent=payload.agent)

    @app.get("/api/chat/history")
    def chat_history(limit: int = 100) -> list[dict]:
        return db.chat_history(limit=limit)

    @app.delete("/api/chat")
    def chat_clear() -> dict:
        db.clear_chat()
        return {"ok": True}

    # ---- Skills --------------------------------------------------------
    @app.get("/api/skills")
    def skills_list() -> list[dict]:
        return skills.discover()

    @app.post("/api/skills/install")
    def skills_install(payload: SkillSource) -> dict:
        try:
            return skills.install(payload.source)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, str(exc))

    @app.post("/api/skills/{name}/run")
    def skills_run(name: str, payload: SkillRun) -> dict:
        return {"name": name, "output": skills.run(name, payload.input)}

    @app.delete("/api/skills/{name}")
    def skills_remove(name: str) -> dict:
        ok = skills.remove(name)
        if not ok:
            raise HTTPException(404, f"no such skill '{name}'")
        return {"ok": True}

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

    # ---- Network info (for phone connection) ---------------------------
    @app.get("/api/network")
    def network_info() -> dict:
        """Return the server's LAN IP so mobile clients can build a URL."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                local_ip = s.getsockname()[0]
        except Exception:
            local_ip = "127.0.0.1"
        port = orchestrator.config.get("server", {}).get("port", 4200) if hasattr(orchestrator, "config") else 4200
        return {
            "local_ip": local_ip,
            "port": port,
            "url": f"http://{local_ip}:{port}",
            "websocket_url": f"ws://{local_ip}:{port}/ws",
        }

    # ---- Execution mode ------------------------------------------------
    @app.get("/api/mode")
    def mode() -> dict:
        is_container = isinstance(orchestrator, ContainerOrchestrator)
        return {
            "mode": "container" if is_container else "thread",
            "description": getattr(app.state, "mode", "thread"),
        }

    # ---- Container orchestrator endpoints (container mode only) --------
    @app.get("/api/containers")
    def containers() -> list:
        if isinstance(orchestrator, ContainerOrchestrator):
            return orchestrator.container_statuses()
        # Thread mode: synthesise virtual "containers" from agent status
        status = db.all_status()
        return [
            {
                "container": f"ae_{a.name}",
                "status": "thread (no Docker)",
                "size": "—",
                "agent": a.name,
            }
            for a in manager.enabled()
        ]

    @app.get("/api/agents/{name}/logs")
    def agent_logs(name: str, lines: int = 80) -> dict:
        if manager.get(name) is None:
            raise HTTPException(404, f"no such agent '{name}'")
        if isinstance(orchestrator, ContainerOrchestrator):
            logs = orchestrator.agent_logs(name, lines=lines)
        else:
            # Thread mode: return recent DB activity for this agent
            rows = db.recent_activity(limit=lines)
            logs = "\n".join(
                f"[{r['ts']}] {r['action']}: {r['detail']}"
                for r in rows
                if r.get("agent") == name
            )
        return {"name": name, "logs": logs}

    # ---- Sandboxes + Computer use --------------------------------------
    @app.get("/api/sandboxes")
    def sandboxes() -> dict:
        import json
        containers_raw = sandbox.sandbox_status()
        browsers_raw = active_browsers()
        try:
            containers = json.loads(containers_raw)
        except Exception:
            containers = []
        try:
            browsers = json.loads(browsers_raw)
        except Exception:
            browsers = []
        return {"containers": containers, "browsers": browsers}

    return app
