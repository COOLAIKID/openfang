"""The Agent: an observe → reason → act → reflect loop driven by an LLM.

An :class:`Agent` wraps a JSON definition file. On each tick the scheduler calls
:meth:`run`, which:

1. **Observe** — gathers the agent's unread messages, recent org activity, the
   current revenue summary, and the agent's own memory.
2. **Reason** — asks the LLM, given its goal + tool catalog + observations, for a
   single JSON action (a tool name + arguments).
3. **Act** — dispatches that tool through :mod:`tools`.
4. **Reflect** — asks the LLM what it learned and whether it should change itself;
   any returned self-edits are applied through the self-modification tools.

Nothing about *what* to do is hardcoded — the behavior emerges from the agent's
goal, its tool set, and the messages flowing through the bus.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import ai_client, database as db, message_bus, tools

ROOT = Path(__file__).resolve().parent.parent


class Agent:
    def __init__(self, path: Path, definition: dict[str, Any]):
        self.path = path
        self.definition = definition

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path) -> "Agent":
        definition = json.loads(path.read_text(encoding="utf-8"))
        definition.setdefault("memory", {})
        definition.setdefault("tools", [])
        definition.setdefault("enabled", True)
        definition.setdefault("interval_minutes", 60)
        definition.setdefault("budget_usd", 0.0)
        return cls(path, definition)

    def save(self) -> None:
        self.path.write_text(json.dumps(self.definition, indent=2, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------------
    # Convenience accessors
    # ------------------------------------------------------------------
    @property
    def name(self) -> str:
        return self.definition["name"]

    @property
    def team(self) -> str:
        return self.definition.get("team", "")

    @property
    def role(self) -> str:
        return self.definition.get("role", "team")

    @property
    def enabled(self) -> bool:
        return bool(self.definition.get("enabled", True))

    @property
    def interval_minutes(self) -> int:
        return int(self.definition.get("interval_minutes", 60))

    @property
    def model_preference(self) -> str | None:
        return self.definition.get("model_preference") or None

    @property
    def allowed_tools(self) -> list[str]:
        return list(self.definition.get("tools", []))

    # ------------------------------------------------------------------
    # The loop
    # ------------------------------------------------------------------
    def run(self) -> str:
        if not self.enabled:
            return "disabled"
        try:
            observation = self._observe()
            action = self._reason(observation)
            result = self._act(action)
            self._reflect(observation, action, result)
            summary = f"{action.get('tool', 'noop')} -> {result[:160]}"
            db.record_run(self.name, summary, error=result.startswith("ERROR"))
            return summary
        except ai_client.AIError as exc:
            msg = f"ERROR: no AI provider available: {exc}"
            db.record_run(self.name, msg, error=True)
            db.log_activity(self.name, "ai_error", str(exc)[:300])
            return msg
        except Exception as exc:  # noqa: BLE001 - never let one agent crash the scheduler
            msg = f"ERROR: {exc}"
            db.record_run(self.name, msg, error=True)
            db.log_activity(self.name, "run_error", str(exc)[:300])
            return msg

    # ------------------------------------------------------------------
    # Phase 1 — observe
    # ------------------------------------------------------------------
    def _observe(self) -> dict[str, Any]:
        msgs = message_bus.inbox(self.name, team=self.team, include_read=False)
        # Mark them read so they aren't reprocessed next tick.
        for m in msgs:
            message_bus.mark_read(m["id"])
        return {
            "messages": [
                {"id": m["id"], "from": m["from_agent"], "type": m["type"], "subject": m["subject"], "body": m["body"]}
                for m in msgs
            ],
            "recent_activity": db.recent_activity(limit=15),
            "revenue": db.revenue_summary(),
            "memory": self.definition.get("memory", {}),
        }

    # ------------------------------------------------------------------
    # Phase 2 — reason
    # ------------------------------------------------------------------
    def _reason(self, observation: dict[str, Any]) -> dict[str, Any]:
        catalog = tools.describe(self.allowed_tools)
        system = self.definition.get("system_prompt", "") + (
            "\n\nYou are part of an autonomous organization whose sole purpose is to "
            "make money for the owner. You act through tools. Respond with STRICT JSON "
            "only — no prose, no code fences."
        )
        prompt = f"""Your goal: {self.definition.get('goal', '')}

Tools you may use:
{catalog}

Current situation:
- Unread messages: {json.dumps(observation['messages'])[:2500]}
- Recent org activity: {json.dumps(observation['recent_activity'])[:1500]}
- Revenue so far: {json.dumps(observation['revenue'])}
- Your memory: {json.dumps(observation['memory'])[:1500]}

Decide the single best action to advance your goal right now.
Respond with JSON exactly like:
{{"reasoning": "one sentence", "tool": "tool_name", "args": {{...}}}}
If no useful action exists, use {{"tool": "noop", "args": {{}}}}."""
        decision = ai_client.ask_json(prompt, system=system, model_preference=self.model_preference)
        if not isinstance(decision, dict) or "tool" not in decision:
            decision = {"tool": "noop", "args": {}, "reasoning": "no valid decision"}
        decision.setdefault("args", {})
        db.log_activity(self.name, "reason", decision.get("reasoning", "")[:200])
        return decision

    # ------------------------------------------------------------------
    # Phase 3 — act
    # ------------------------------------------------------------------
    def _act(self, action: dict[str, Any]) -> str:
        tool_name = action.get("tool", "noop")
        if tool_name == "noop":
            return "noop"
        if tool_name not in self.allowed_tools:
            return f"ERROR: tool '{tool_name}' not in your allowed set."
        args = action.get("args") or {}
        if not isinstance(args, dict):
            return "ERROR: args must be an object."
        return tools.dispatch(tool_name, self.name, args)

    # ------------------------------------------------------------------
    # Phase 4 — reflect (and optionally self-modify)
    # ------------------------------------------------------------------
    def _reflect(self, observation: dict[str, Any], action: dict[str, Any], result: str) -> None:
        # Only agents that hold self-modification tools bother reflecting on change.
        self_tools = [t for t in self.allowed_tools if t.startswith(("update_", "spawn_", "kill_", "set_budget"))]
        if not self_tools:
            return
        system = (
            "You are reflecting on your own performance as an autonomous money-making "
            "agent. You may adjust yourself using self-modification tools. Respond with "
            "STRICT JSON only."
        )
        prompt = f"""You just took this action: {json.dumps(action)}
The result was: {result[:800]}

Self-modification tools available to you:
{tools.describe(self_tools)}

If a change would make you more effective, return it. Otherwise return an empty list.
Respond with JSON exactly like:
{{"changes": [{{"tool": "update_interval", "args": {{"minutes": 30}}}}]}}"""
        try:
            reflection = ai_client.ask_json(prompt, system=system, model_preference=self.model_preference)
        except ai_client.AIError:
            return
        for change in (reflection.get("changes") or [])[:3]:
            tool_name = change.get("tool")
            if tool_name in self_tools:
                out = tools.dispatch(tool_name, self.name, change.get("args") or {})
                db.log_activity(self.name, "reflect_change", f"{tool_name}: {out[:120]}")
