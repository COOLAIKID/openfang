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

CREATE TABLE IF NOT EXISTS tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    agent      TEXT NOT NULL,
    tool       TEXT,
    title      TEXT NOT NULL,        -- human friendly, present then past tense
    detail     TEXT,                 -- why / what (one sentence)
    status     TEXT NOT NULL DEFAULT 'running',  -- running | done | error
    result     TEXT,
    started_at REAL NOT NULL,
    ended_at   REAL
);

CREATE TABLE IF NOT EXISTS machines (
    name       TEXT PRIMARY KEY,     -- e.g. "Aaron's MacBook"
    info       TEXT,                 -- json: os, docker?, agents running, etc.
    last_seen  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS runner_jobs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    machine    TEXT NOT NULL,
    kind       TEXT NOT NULL,        -- shell | agent
    payload    TEXT,                 -- command, or agent name
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending|running|done|error
    result     TEXT,
    created_at REAL NOT NULL,
    updated_at REAL
);

CREATE INDEX IF NOT EXISTS idx_messages_to ON messages(to_agent, status);
CREATE INDEX IF NOT EXISTS idx_activity_agent ON activity(agent);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_jobs_machine ON runner_jobs(machine, status);
"""


def get_db_path() -> Path:
    """Return the path to the shared SQLite database file."""
    return DB_PATH


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
# Tasks — real-time "what is each agent doing" timeline
# --------------------------------------------------------------------------
def start_task(agent: str, tool: str, title: str, detail: str = "") -> int:
    """Record a task the agent just started. Returns its id so it can be finished."""
    init()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO tasks (agent, tool, title, detail, status, started_at) "
            "VALUES (?,?,?,?, 'running', ?)",
            (agent, tool, title, detail, _now()),
        )
        return int(cur.lastrowid)


def finish_task(task_id: int, status: str, title: str | None = None, result: str = "") -> None:
    """Mark a task done/error. Optionally rewrite the title to past tense."""
    init()
    with _connect() as conn:
        if title is not None:
            conn.execute(
                "UPDATE tasks SET status=?, title=?, result=?, ended_at=? WHERE id=?",
                (status, title, result, _now(), task_id),
            )
        else:
            conn.execute(
                "UPDATE tasks SET status=?, result=?, ended_at=? WHERE id=?",
                (status, result, _now(), task_id),
            )


def recent_tasks(limit: int = 40, agent: str | None = None) -> list[dict[str, Any]]:
    init()
    with _connect() as conn:
        if agent:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE agent=? ORDER BY id DESC LIMIT ?", (agent, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [dict(r) for r in rows]


def cleanup_stale_tasks(max_age_seconds: float = 1800) -> None:
    """Mark long-running 'running' tasks as done (e.g. after a crash/restart)."""
    init()
    cutoff = _now() - max_age_seconds
    with _connect() as conn:
        conn.execute(
            "UPDATE tasks SET status='done', ended_at=? WHERE status='running' AND started_at < ?",
            (_now(), cutoff),
        )


# --------------------------------------------------------------------------
# Machines + remote jobs (your computer connecting to the cloud dashboard)
# --------------------------------------------------------------------------
def register_machine(name: str, info: str = "") -> None:
    """Upsert a connected machine and bump its heartbeat."""
    init()
    with _connect() as conn:
        if info:
            conn.execute(
                "INSERT INTO machines (name, info, last_seen) VALUES (?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET info=excluded.info, last_seen=excluded.last_seen",
                (name, info, _now()),
            )
        else:
            conn.execute(
                "INSERT INTO machines (name, info, last_seen) VALUES (?, '', ?) "
                "ON CONFLICT(name) DO UPDATE SET last_seen=excluded.last_seen",
                (name, _now()),
            )


def recent_machines(online_within: float = 45.0) -> list[dict[str, Any]]:
    init()
    with _connect() as conn:
        rows = conn.execute("SELECT * FROM machines ORDER BY last_seen DESC").fetchall()
    out = []
    now = _now()
    for r in rows:
        d = dict(r)
        d["online"] = (now - d["last_seen"]) <= online_within
        out.append(d)
    return out


def enqueue_job(machine: str, kind: str, payload: str = "") -> int:
    init()
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO runner_jobs (machine, kind, payload, status, created_at) "
            "VALUES (?,?,?, 'pending', ?)",
            (machine, kind, payload, _now()),
        )
        return int(cur.lastrowid)


def claim_next_job(machine: str) -> dict[str, Any] | None:
    """Atomically hand the oldest pending job for a machine to its runner."""
    init()
    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM runner_jobs WHERE machine=? AND status='pending' ORDER BY id ASC LIMIT 1",
            (machine,),
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        conn.execute(
            "UPDATE runner_jobs SET status='running', updated_at=? WHERE id=?",
            (_now(), row["id"]),
        )
        conn.execute("COMMIT")
        return dict(row)


def complete_job(job_id: int, status: str, result: str = "") -> None:
    init()
    with _connect() as conn:
        conn.execute(
            "UPDATE runner_jobs SET status=?, result=?, updated_at=? WHERE id=?",
            (status, result, _now(), job_id),
        )


def recent_jobs(limit: int = 30, machine: str | None = None) -> list[dict[str, Any]]:
    init()
    with _connect() as conn:
        if machine:
            rows = conn.execute(
                "SELECT * FROM runner_jobs WHERE machine=? ORDER BY id DESC LIMIT ?",
                (machine, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM runner_jobs ORDER BY id DESC LIMIT ?", (limit,)
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
