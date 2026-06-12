"""Self-modification tools — agents reshaping the organization at runtime.

These tools mutate the JSON agent-definition files on disk (and, for spawn/kill,
register/unregister with the live manager). They are exposed to agents through
the same :func:`tools.dispatch` mechanism, so an agent can decide, mid-run, to
switch its own model, change its cadence, grow new tools, or spin up a whole new
agent.

To avoid runaway growth there are soft guard rails: a maximum agent count and a
floor on run interval. They protect the host machine, not the agents' autonomy.
"""
from __future__ import annotations

import json
from typing import Any

from . import database as db
from .tools import tool

# Guard rails (generous — these exist to protect the host, not to constrain ideas).
MAX_AGENTS = 60
MIN_INTERVAL_MINUTES = 5

# Wired up by agent_manager at startup to avoid an import cycle.
_manager = None


def bind_manager(manager: Any) -> None:
    global _manager
    _manager = manager


def _require_manager():
    if _manager is None:
        raise RuntimeError("agent manager not initialized")
    return _manager


def _patch(agent_name: str, **changes: Any) -> str:
    mgr = _require_manager()
    agent = mgr.get(agent_name)
    if agent is None:
        return f"ERROR: no such agent '{agent_name}'."
    agent.definition.update(changes)
    agent.save()
    mgr.reschedule(agent_name)
    db.log_activity(agent_name, "self_modify", json.dumps(changes)[:300])
    return f"Updated {agent_name}: {', '.join(changes)}"


# --------------------------------------------------------------------------
# Field-level self edits
# --------------------------------------------------------------------------
# The decorator injects agent=caller. Several self-tools accept an explicit
# `agent_name` argument naming WHICH agent to edit, which may differ from the
# caller (e.g. the CFO editing a team member's budget). When omitted, the caller
# edits itself.
@tool("update_goal", "update_goal(agent_name?, new_goal) — rewrite an agent's goal.")
def _update_goal(agent: str, new_goal: str = "", agent_name: str | None = None, **_: Any) -> str:
    return _patch(agent_name or agent, goal=new_goal)


@tool("update_system_prompt", "update_system_prompt(agent_name?, new_prompt) — rewrite a system prompt.")
def _update_system_prompt(agent: str, new_prompt: str = "", agent_name: str | None = None, **_: Any) -> str:
    return _patch(agent_name or agent, system_prompt=new_prompt)


@tool("update_model", "update_model(agent_name?, model_id) — switch AI model, e.g. 'groq/llama-3.3-70b-versatile' or 'ollama/mistral'.")
def _update_model(agent: str, model_id: str = "", agent_name: str | None = None, **_: Any) -> str:
    return _patch(agent_name or agent, model_preference=model_id)


@tool("update_interval", "update_interval(agent_name?, minutes) — change how often an agent runs.")
def _update_interval(agent: str, minutes: int = 60, agent_name: str | None = None, **_: Any) -> str:
    minutes = max(int(minutes), MIN_INTERVAL_MINUTES)
    return _patch(agent_name or agent, interval_minutes=minutes)


@tool("update_tools", "update_tools(agent_name?, tools) — replace an agent's tool list (array of tool names).")
def _update_tools(agent: str, tools: list | None = None, agent_name: str | None = None, **_: Any) -> str:
    if not isinstance(tools, list):
        return "ERROR: 'tools' must be a list of tool names."
    return _patch(agent_name or agent, tools=tools)


@tool("update_memory", "update_memory(key, value) — persist a key/value into your own memory.")
def _update_memory(agent: str, key: str = "", value: Any = None, agent_name: str | None = None, **_: Any) -> str:
    mgr = _require_manager()
    target = mgr.get(agent_name or agent)
    if target is None:
        return f"ERROR: no such agent '{agent_name or agent}'."
    memory = dict(target.definition.get("memory", {}))
    memory[key] = value
    return _patch(agent_name or agent, memory=memory)


@tool("set_budget", "set_budget(agent_name, usd) — set an agent's spending budget in USD.")
def _set_budget(agent: str, agent_name: str = "", usd: float = 0.0, **_: Any) -> str:
    return _patch(agent_name or agent, budget_usd=float(usd))


# --------------------------------------------------------------------------
# Org-level changes
# --------------------------------------------------------------------------
@tool(
    "spawn_agent",
    "spawn_agent(name, goal, role?, team?, tools?, interval_minutes?, system_prompt?, model_preference?) — create a brand new agent.",
)
def _spawn_agent(
    agent: str,
    name: str = "",
    goal: str = "",
    role: str = "team",
    team: str = "",
    tools: list | None = None,
    interval_minutes: int = 60,
    system_prompt: str = "",
    model_preference: str = "",
    **_: Any,
) -> str:
    mgr = _require_manager()
    if len(mgr.all()) >= MAX_AGENTS:
        return f"ERROR: agent cap reached ({MAX_AGENTS}). Kill an agent before spawning more."
    if not name:
        return "ERROR: 'name' is required."
    if mgr.get(name) is not None:
        return f"ERROR: agent '{name}' already exists."

    definition = {
        "name": name,
        "team": team,
        "role": role,
        "enabled": True,
        "goal": goal,
        "system_prompt": system_prompt or f"You are the {name} agent. {goal}",
        "model_preference": model_preference or "",
        "interval_minutes": max(int(interval_minutes), MIN_INTERVAL_MINUTES),
        "budget_usd": 0.0,
        "tools": tools if isinstance(tools, list) else ["web_search", "send_message", "get_messages", "save_output"],
        "memory": {},
    }
    mgr.spawn(definition, created_by=agent)
    db.log_activity(agent, "spawn_agent", name)
    return f"Spawned agent '{name}' (role={role}, team={team})."


@tool("kill_agent", "kill_agent(agent_name) — disable an agent (it stops running).")
def _kill_agent(agent: str, agent_name: str = "", **_: Any) -> str:
    mgr = _require_manager()
    if not agent_name:
        return "ERROR: 'agent_name' is required."
    target = mgr.get(agent_name)
    if target is None:
        return f"ERROR: no such agent '{agent_name}'."
    mgr.kill(agent_name)
    db.log_activity(agent, "kill_agent", agent_name)
    return f"Killed agent '{agent_name}'."


@tool("get_all_agents", "get_all_agents() — list every agent with its role, team, model and goal.")
def _get_all_agents(agent: str, **_: Any) -> str:
    mgr = _require_manager()
    out = []
    for a in mgr.all():
        d = a.definition
        out.append(
            {
                "name": d["name"],
                "role": d.get("role"),
                "team": d.get("team"),
                "enabled": d.get("enabled", True),
                "model": d.get("model_preference") or "auto",
                "interval_minutes": d.get("interval_minutes"),
                "budget_usd": d.get("budget_usd", 0.0),
                "goal": d.get("goal", ""),
            }
        )
    return json.dumps(out)
