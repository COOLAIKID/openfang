"""Tests for database, message bus, skills, tools dispatch, and agent loading."""
from __future__ import annotations

import json

from core import message_bus, skills, tools


# -- database ---------------------------------------------------------------
def test_activity_log(temp_db):
    temp_db.log_activity("agentx", "did_thing", "detail")
    rows = temp_db.recent_activity(limit=5)
    assert rows[0]["agent"] == "agentx"


def test_revenue_summary(temp_db):
    temp_db.log_revenue(10.0, source="ads", agent="a")
    temp_db.log_revenue(5.5, source="ads", agent="b")
    summary = temp_db.revenue_summary()
    assert summary["total_usd"] == 15.5


def test_record_run_increments(temp_db):
    temp_db.record_run("agz", "ok")
    temp_db.record_run("agz", "ok again")
    status = temp_db.all_status()
    assert status["agz"]["runs"] == 2


def test_chat_history(temp_db):
    temp_db.add_chat("user", "hello")
    temp_db.add_chat("assistant", "hi", agent="ceo")
    hist = temp_db.chat_history()
    assert [m["role"] for m in hist] == ["user", "assistant"]


# -- message bus ------------------------------------------------------------
def test_message_bus_send_inbox(temp_db):
    mid = message_bus.send("a", "b", message_bus.WORK_ITEM, subject="s", body="x")
    assert mid > 0
    inbox = message_bus.inbox("b")
    assert inbox[0]["subject"] == "s"


def test_message_mark_read(temp_db):
    message_bus.send("a", "b", "directive", subject="d")
    msg = message_bus.inbox("b")[0]
    message_bus.mark_read(msg["id"])
    assert message_bus.inbox("b", include_read=False) == []


# -- skills -----------------------------------------------------------------
def test_skills_discover_has_seo_article():
    names = {s["name"] for s in skills.discover()}
    assert "seo-article" in names


def test_skill_get_includes_instructions():
    s = skills.get("seo-article")
    assert s is not None
    assert len(s["instructions"]) > 100


def test_skill_frontmatter_parse():
    meta, body = skills._parse_frontmatter("---\nname: x\ndescription: y\n---\nbody here")
    assert meta["name"] == "x"
    assert body == "body here"


# -- tools ------------------------------------------------------------------
def test_tool_dispatch_unknown():
    out = tools.dispatch("no_such_tool", "agent", {})
    assert out.startswith("ERROR")


def test_tool_dispatch_make_slug():
    out = tools.dispatch("make_slug", "agent", {"title": "My Cool Post"})
    assert out == "my-cool-post"


def test_tool_describe():
    desc = tools.describe(["make_slug", "web_search"])
    assert "make_slug" in desc and "web_search" in desc


# -- agents -----------------------------------------------------------------
def test_all_seed_agents_load():
    from core.agent_manager import AgentManager

    m = AgentManager()
    m.discover()
    assert len(m.all()) >= 21


def test_every_agent_tool_exists():
    from core.agent_manager import AgentManager

    m = AgentManager()
    m.discover()
    missing = [(a.name, t) for a in m.all() for t in a.allowed_tools if t not in tools.REGISTRY]
    assert missing == []
