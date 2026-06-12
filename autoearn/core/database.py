"""SQLite persistence for the whole organization.

Everything lives in a single local file (``autoearn.db``) next to the project
root: the message bus, the activity log, revenue events, and per-run agent
status. Agent *definitions* live as JSON files on disk (so agents can rewrite
themselves); this DB holds the runtime state.

The module exposes a tiny set of helpers rather than an ORM — every call opens a
short-lived connection so it is safe to use from the scheduler's worker threads.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "autoearn.db"

_init_lock = threading.Lock()
_initialized = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    from_agent TEXT NOT NULL,
    to_agent   TEXT NOT NULL,
    type       TEXT NOT NULL,
    subject    TEXT,
    body       TEXT,
    meta       TEXT,
    status     TEXT NOT NULL DEFAULT 'unread',
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS activity (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent      TEXT NOT NULL,
    action     TEXT NOT NULL,
    detail     TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS revenue (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent      TEXT,
    source     TEXT,
    amount_usd REAL NOT NULL,
    note       TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_status (
    name        TEXT PRIMARY KEY,
    last_run    REAL,
    last_result TEXT,
    runs        INTEGER NOT NULL DEFAULT 0,
    errors      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS chat (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    role       TEXT NOT NULL,        -- 'user' | 'assistant' | 'system'
    agent      TEXT,                 -- which agent answered (or was addressed)
    content    TEXT NOT NULL,
    kind       TEXT DEFAULT 'text',  -- 'text' | 'command' | 'error'
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_to ON messages(to_agent, status);
CREATE INDEX IF NOT EXISTS idx_activity_agent ON activity(agent);
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init() -> None:
    """Create tables if they do not exist (idempotent, thread-safe)."""
    global _initialized
    with _init_lock:
        if _initialized:
            return
        with _connect() as conn:
            conn.executescript(_SCHEMA)
        _initialized = True


def _now() -> float:
    import time

    return time.time()


# --------------------------------------------------------------------------
# Activity log
# --------------------------------------------------------------------------
def log_activity(agent: str, action: str, detail: str = "") -> None:
    init()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO activity (agent, action, detail, created_at) VALUES (?,?,?,?)",
            (agent, action, detail, _now()),
        )


def recent_activity(limit: int = 50, agent: str | None = None) -> list[dict[str, Any]]:
    init()
    with _connect() as conn:
        if agent:
            rows = conn.execute(
                "SELECT * FROM activity WHERE agent=? ORDER BY id DESC LIMIT ?",
                (agent, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# Revenue
# --------------------------------------------------------------------------
def log_revenue(amount_usd: float, source: str = "", agent: str = "", note: str = "") -> None:
    init()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO revenue (agent, source, amount_usd, note, created_at) VALUES (?,?,?,?,?)",
            (agent, source, float(amount_usd), note, _now()),
        )


def revenue_summary() -> dict[str, Any]:
    init()
    with _connect() as conn:
        total = conn.execute("SELECT COALESCE(SUM(amount_usd),0) AS t FROM revenue").fetchone()["t"]
        by_source = conn.execute(
            "SELECT source, COALESCE(SUM(amount_usd),0) AS amount FROM revenue GROUP BY source ORDER BY amount DESC"
        ).fetchall()
        by_agent = conn.execute(
            "SELECT agent, COALESCE(SUM(amount_usd),0) AS amount FROM revenue GROUP BY agent ORDER BY amount DESC"
        ).fetchall()
    return {
        "total_usd": round(total, 4),
        "by_source": [dict(r) for r in by_source],
        "by_agent": [dict(r) for r in by_agent],
    }


# --------------------------------------------------------------------------
# Agent run status
# --------------------------------------------------------------------------
def record_run(name: str, result: str, error: bool = False) -> None:
    init()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO agent_status (name, last_run, last_result, runs, errors)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(name) DO UPDATE SET
                last_run=excluded.last_run,
                last_result=excluded.last_result,
                runs=agent_status.runs + 1,
                errors=agent_status.errors + ?
            """,
            (name, _now(), result[:2000], 1 if error else 0, 1 if error else 0),
        )


def all_status() -> dict[str, dict[str, Any]]:
    init()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM agent_status").fetchall()
    return {r["name"]: dict(r) for r in rows}


# --------------------------------------------------------------------------
# Raw access for the message bus
# --------------------------------------------------------------------------
# --------------------------------------------------------------------------
# Chat history
# --------------------------------------------------------------------------
def add_chat(role: str, content: str, agent: str = "", kind: str = "text") -> int:
    init()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO chat (role, agent, content, kind, created_at) VALUES (?,?,?,?,?)",
            (role, agent, content, kind, _now()),
        )
        return int(cur.lastrowid or 0)


def chat_history(limit: int = 100) -> list[dict[str, Any]]:
    init()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM chat ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def clear_chat() -> None:
    init()
    with _connect() as conn:
        conn.execute("DELETE FROM chat")


def execute(sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    """Run a statement and return rows as dicts (empty for writes)."""
    init()
    with _connect() as conn:
        cur = conn.execute(sql, tuple(params))
        if sql.lstrip().upper().startswith("SELECT"):
            return [dict(r) for r in cur.fetchall()]
        return []


def insert(sql: str, params: Iterable[Any] = ()) -> int:
    """Run an INSERT and return the new row id (same connection, so lastrowid is valid)."""
    init()
    with _connect() as conn:
        cur = conn.execute(sql, tuple(params))
        return int(cur.lastrowid or 0)


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
