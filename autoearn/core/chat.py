"""Chat backend: the user talks to the organization.

Plain messages get a conversational reply from a chosen agent (default: the CEO),
grounded with live org context (revenue, agents, recent activity). Messages that
start with ``/`` are slash commands that perform real, deterministic actions
against the org — no LLM required, so they work even with no AI key configured.

The handler returns a dict ``{role, agent, content, kind}`` which the dashboard
renders as a chat bubble.
"""
from __future__ import annotations

import json
import shlex
from typing import Any, Callable

from . import ai_client, database as db, message_bus, skills

# Registry of slash commands: name -> (handler, help text).
_COMMANDS: dict[str, tuple[Callable, str]] = {}


def command(name: str, help_text: str) -> Callable:
    def wrap(fn: Callable) -> Callable:
        _COMMANDS[name] = (fn, help_text)
        return fn

    return wrap


class Chat:
    """Bound to the live orchestrator so commands can act on the org."""

    def __init__(self, orchestrator) -> None:
        self.orchestrator = orchestrator
        self.manager = orchestrator.manager

    # ------------------------------------------------------------------
    def handle(self, message: str, agent: str = "ceo") -> dict[str, Any]:
        message = (message or "").strip()
        db.add_chat("user", message, agent=agent)
        if message.startswith("/"):
            reply = self._command(message)
        else:
            reply = self._converse(message, agent)
        db.add_chat(reply["role"], reply["content"], agent=reply.get("agent", ""), kind=reply.get("kind", "text"))
        return reply

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------
    def _command(self, message: str) -> dict[str, Any]:
        try:
            parts = shlex.split(message[1:])
        except ValueError:
            parts = message[1:].split()
        if not parts:
            return _msg("Type /help for available commands.", kind="command")
        name, args = parts[0].lower(), parts[1:]
        entry = _COMMANDS.get(name)
        if entry is None:
            return _msg(f"Unknown command '/{name}'. Type /help.", kind="error")
        try:
            return entry[0](self, args)
        except Exception as exc:  # noqa: BLE001
            return _msg(f"Command failed: {exc}", kind="error")

    # ------------------------------------------------------------------
    # Conversation
    # ------------------------------------------------------------------
    def _converse(self, message: str, agent_name: str) -> dict[str, Any]:
        agent = self.manager.get(agent_name)
        persona = agent.definition.get("system_prompt", "") if agent else ""
        name = agent.name if agent else "assistant"
        revenue = db.revenue_summary()
        roster = [
            {"name": a.name, "role": a.role, "team": a.team, "goal": a.definition.get("goal", "")}
            for a in self.manager.all()
        ]
        history = db.chat_history(limit=12)
        convo = "\n".join(f"{m['role']}: {m['content']}" for m in history[:-1])
        system = (
            (persona or "You are the assistant for an autonomous AI money-making organization.")
            + "\n\nYou are speaking with the human owner through a chat console. Be concise, "
            "concrete and helpful. You can describe what the organization is doing and advise "
            "on strategy. To take direct actions the owner can use slash commands (tell them "
            "about /help if relevant)."
        )
        prompt = (
            f"Live organization state:\n"
            f"- Total revenue: ${revenue['total_usd']}\n"
            f"- Agents ({len(roster)}): {json.dumps(roster)[:1800]}\n\n"
            f"Recent conversation:\n{convo}\n\n"
            f"Owner: {message}\n{name}:"
        )
        try:
            result = ai_client.ask(prompt, system=system, model_preference=agent.model_preference if agent else None)
            return _msg(result.text, agent=name)
        except ai_client.AIError as exc:
            return _msg(
                "No AI provider is reachable right now, so I can't chat freely — but slash "
                f"commands still work. Try /agents or /revenue. ({exc})",
                agent=name,
                kind="error",
            )


def _msg(content: str, agent: str = "", kind: str = "text") -> dict[str, Any]:
    return {"role": "assistant", "agent": agent, "content": content, "kind": kind}


# --------------------------------------------------------------------------
# Command implementations
# --------------------------------------------------------------------------
@command("help", "/help — list all commands.")
def _help(chat: Chat, args: list[str]) -> dict[str, Any]:
    lines = ["**Slash commands:**"] + sorted(help_text for _, (_, help_text) in _COMMANDS.items())
    return _msg("\n".join(lines), kind="command")


@command("agents", "/agents — list every agent with role, model and run stats.")
def _agents(chat: Chat, args: list[str]) -> dict[str, Any]:
    status = db.all_status()
    rows = []
    for a in chat.manager.all():
        st = status.get(a.name, {})
        rows.append(f"- **{a.name}** ({a.role}{'/' + a.team if a.team else ''}) · {a.definition.get('model_preference') or 'auto'} · runs {st.get('runs',0)} errs {st.get('errors',0)}")
    return _msg(f"**{len(rows)} agents:**\n" + "\n".join(rows), kind="command")


@command("revenue", "/revenue — show total and per-source revenue.")
def _revenue(chat: Chat, args: list[str]) -> dict[str, Any]:
    r = db.revenue_summary()
    src = "\n".join(f"- {s['source'] or '(unattributed)'}: ${s['amount']:.2f}" for s in r["by_source"]) or "- none yet"
    return _msg(f"**Total revenue: ${r['total_usd']:.2f}**\nBy source:\n{src}", kind="command")


@command("trigger", "/trigger <agent> — run an agent immediately.")
def _trigger(chat: Chat, args: list[str]) -> dict[str, Any]:
    if not args:
        return _msg("Usage: /trigger <agent>", kind="error")
    result = chat.orchestrator.trigger_now(args[0])
    return _msg(f"Ran **{args[0]}** → {result}", kind="command")


@command("spawn", "/spawn <name> <role> <team> <goal...> — create a new agent.")
def _spawn(chat: Chat, args: list[str]) -> dict[str, Any]:
    if len(args) < 4:
        return _msg("Usage: /spawn <name> <role:council|team|qc> <team> <goal...>", kind="error")
    name, role, team = args[0], args[1], args[2]
    goal = " ".join(args[3:])
    from . import tools

    out = tools.dispatch(
        "spawn_agent", "owner",
        {"name": name, "role": role, "team": team, "goal": goal,
         "tools": ["web_search", "send_message", "get_messages", "save_output"]},
    )
    return _msg(out, kind="command")


@command("kill", "/kill <agent> — disable an agent.")
def _kill(chat: Chat, args: list[str]) -> dict[str, Any]:
    if not args:
        return _msg("Usage: /kill <agent>", kind="error")
    from . import tools

    return _msg(tools.dispatch("kill_agent", "owner", {"agent_name": args[0]}), kind="command")


@command("directive", "/directive <team> <text...> — send a directive to a team (or agent).")
def _directive(chat: Chat, args: list[str]) -> dict[str, Any]:
    if len(args) < 2:
        return _msg("Usage: /directive <team-or-agent> <text...>", kind="error")
    target = args[0]
    body = " ".join(args[1:])
    mid = message_bus.send("owner", target, message_bus.DIRECTIVE, subject="owner directive", body=body)
    return _msg(f"Directive #{mid} sent to **{target}**.", kind="command")


@command("skills", "/skills — list installed Claude skills.")
def _skills(chat: Chat, args: list[str]) -> dict[str, Any]:
    items = skills.discover()
    if not items:
        return _msg("No skills installed. Install one with `/skill install <path|git-url|zip-url>`.", kind="command")
    rows = "\n".join(f"- **{s['name']}** — {s['description']}" for s in items)
    return _msg(f"**{len(items)} skills installed:**\n{rows}", kind="command")


@command("skill", "/skill install|remove|run|info <name|source> [input...] — manage skills.")
def _skill(chat: Chat, args: list[str]) -> dict[str, Any]:
    if not args:
        return _msg("Usage: /skill install <source> | remove <name> | run <name> <input> | info <name>", kind="error")
    sub = args[0].lower()
    rest = args[1:]
    if sub == "install":
        if not rest:
            return _msg("Usage: /skill install <local-path|git-url|zip-url>", kind="error")
        skill = skills.install(rest[0])
        return _msg(f"Installed **{skill['name']}** — {skill['description']}", kind="command")
    if sub == "remove":
        ok = skills.remove(rest[0]) if rest else False
        return _msg(f"Removed '{rest[0]}'." if ok else "No such skill.", kind="command")
    if sub == "info":
        s = skills.get(rest[0]) if rest else None
        if not s:
            return _msg("No such skill.", kind="error")
        return _msg(f"**{s['name']}**\n{s['description']}\n\nFiles: {', '.join(s['files']) or '—'}", kind="command")
    if sub == "run":
        if len(rest) < 1:
            return _msg("Usage: /skill run <name> <input...>", kind="error")
        return _msg(skills.run(rest[0], " ".join(rest[1:])), kind="command")
    return _msg(f"Unknown subcommand '{sub}'.", kind="error")


@command("clear", "/clear — clear the chat history.")
def _clear(chat: Chat, args: list[str]) -> dict[str, Any]:
    db.clear_chat()
    return _msg("Chat history cleared.", kind="command")
