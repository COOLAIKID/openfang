"""Tests for the chat slash-command engine (no AI calls)."""
from __future__ import annotations

from core.agent_manager import AgentManager
from core.chat import Chat


class _FakeOrchestrator:
    def __init__(self):
        self.manager = AgentManager()
        self.manager.discover()

    def trigger_now(self, name):
        return f"ran {name}"


def _chat():
    return Chat(_FakeOrchestrator())


def test_help_lists_commands(temp_db):
    out = _chat().handle("/help")
    assert out["kind"] == "command"
    assert "/agents" in out["content"]


def test_agents_command(temp_db):
    out = _chat().handle("/agents")
    assert "agents" in out["content"].lower()


def test_revenue_command(temp_db):
    out = _chat().handle("/revenue")
    assert "Total revenue" in out["content"]


def test_unknown_command(temp_db):
    out = _chat().handle("/floopdoop")
    assert out["kind"] == "error"


def test_skills_command(temp_db):
    out = _chat().handle("/skills")
    assert "seo-article" in out["content"]


def test_trigger_command(temp_db):
    out = _chat().handle("/trigger ceo")
    assert "ceo" in out["content"]


def test_spawn_then_kill(temp_db):
    c = _chat()
    spawn = c.handle("/spawn testbot team market just a test goal")
    assert "Spawned" in spawn["content"] or "exists" in spawn["content"]
    kill = c.handle("/kill testbot")
    assert "Killed" in kill["content"] or "no such" in kill["content"]
    # cleanup spawned file if present
    from pathlib import Path

    p = Path(__file__).resolve().parent.parent / "teams" / "market" / "testbot.json"
    if p.exists():
        p.unlink()


def test_history_persisted(temp_db):
    c = _chat()
    c.handle("/revenue")
    hist = temp_db.chat_history()
    assert len(hist) >= 2  # user + assistant
