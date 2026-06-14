"""Inter-agent message bus, backed by the SQLite ``messages`` table.

Agents communicate exclusively through this bus. The council issues
``directive`` messages, teams hand ``work_item`` / ``output`` messages down their
chain, and QC replies with ``approval`` or ``rejection``. Messages addressed to a
team name (e.g. ``content``) are readable by any member of that team.
"""
from __future__ import annotations

import json
import time
from typing import Any

from . import database as db

# Recognized message types (free-form, but these are what the agents use).
DIRECTIVE = "directive"
WORK_ITEM = "work_item"
OUTPUT = "output"
REVIEW = "review"
APPROVAL = "approval"
REJECTION = "rejection"


def send(
    from_agent: str,
    to_agent: str,
    type: str,
    subject: str = "",
    body: str = "",
    meta: dict[str, Any] | None = None,
) -> int:
    """Post a message. Returns the new message id."""
    db.init()
    new_id = db.insert(
        """
        INSERT INTO messages (from_agent, to_agent, type, subject, body, meta, status, created_at)
        VALUES (?,?,?,?,?,?, 'unread', ?)
        """,
        (from_agent, to_agent, type, subject, body, json.dumps(meta or {}), time.time()),
    )
    db.log_activity(from_agent, f"sent:{type}", f"-> {to_agent}: {subject}")
    return new_id


def inbox(agent: str, team: str | None = None, include_read: bool = False) -> list[dict[str, Any]]:
    """Messages addressed to this agent (or its team), newest first."""
    db.init()
    targets = [agent]
    if team:
        targets.append(team)
    placeholders = ",".join("?" for _ in targets)
    status_clause = "" if include_read else "AND status = 'unread'"
    rows = db.execute(
        f"SELECT * FROM messages WHERE to_agent IN ({placeholders}) {status_clause} ORDER BY id DESC",
        targets,
    )
    for r in rows:
        r["meta"] = _loads(r.get("meta"))
    return rows


def mark_read(message_id: int) -> None:
    db.execute("UPDATE messages SET status='read' WHERE id=?", (message_id,))


def recent(limit: int = 50) -> list[dict[str, Any]]:
    """Recent bus traffic for the dashboard feed."""
    rows = db.execute("SELECT * FROM messages ORDER BY id DESC LIMIT ?", (limit,))
    for r in rows:
        r["meta"] = _loads(r.get("meta"))
    return rows


def thread_count(subject: str, type: str) -> int:
    """How many messages of a type share a subject — used to cap QC retries."""
    rows = db.execute(
        "SELECT COUNT(*) AS c FROM messages WHERE subject=? AND type=?", (subject, type)
    )
    return rows[0]["c"] if rows else 0


def _loads(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
